"""Collect a runner-local observation snapshot from the configured external account.

This module is intentionally stateless. It does not read private production state
and emits only aggregate/raw service measurements for later private processing.
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PAGE_SIZE = 200
RECENT_DAYS = 10
WINDOW_DAYS = 90
LOCAL_TZ = ZoneInfo("Asia/Singapore")
METRICS = (
    "views,engagedViews,likes,comments,shares,estimatedMinutesWatched,"
    "averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost"
)


def _instant(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _credentials():
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=None,
        refresh_token=os.environ["RUNTIME_AUTH_C"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["RUNTIME_AUTH_A"],
        client_secret=os.environ["RUNTIME_AUTH_B"],
        scopes=[
            "https://www.googleapis.com/auth/yt-analytics.readonly",
            "https://www.googleapis.com/auth/youtube.readonly",
        ],
    )


def collect_aggregate(credentials, start_date, end_date):
    from googleapiclient.discovery import build

    service = build(
        "youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False
    )
    rows = []
    start_index = 1
    while True:
        result = service.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics=METRICS,
            dimensions="video",
            sort="-views",
            maxResults=PAGE_SIZE,
            startIndex=start_index,
        ).execute()
        headers = [column["name"] for column in result.get("columnHeaders", [])]
        page = [dict(zip(headers, values)) for values in result.get("rows", []) or []]
        for row in page:
            row["video"] = str(row.get("video", "")).strip()
            row["data_source"] = "aggregate"
        rows.extend(row for row in page if row["video"])
        if len(page) < PAGE_SIZE:
            break
        start_index += PAGE_SIZE
    return rows


def recent_video_ids(youtube, now_utc):
    channel = youtube.channels().list(
        part="contentDetails", mine=True, maxResults=1
    ).execute()
    items = channel.get("items", []) or []
    if len(items) != 1:
        raise RuntimeError("Expected exactly one authenticated channel")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    cutoff = now_utc - timedelta(days=RECENT_DAYS)
    token = None
    video_ids = []
    while True:
        kwargs = {
            "part": "contentDetails",
            "playlistId": uploads,
            "maxResults": 50,
        }
        if token:
            kwargs["pageToken"] = token
        result = youtube.playlistItems().list(**kwargs).execute()
        stop = False
        for item in result.get("items", []) or []:
            details = item.get("contentDetails", {})
            published = _instant(details.get("videoPublishedAt"))
            if published is not None and published < cutoff:
                stop = True
                break
            video_id = str(details.get("videoId", "")).strip()
            if video_id:
                video_ids.append(video_id)
        if stop:
            break
        token = result.get("nextPageToken")
        if not token:
            break
    return list(dict.fromkeys(video_ids))


def collect_recent(credentials, now_utc):
    from googleapiclient.discovery import build

    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    video_ids = recent_video_ids(youtube, now_utc)
    rows = []
    for offset in range(0, len(video_ids), 50):
        batch = video_ids[offset : offset + 50]
        result = youtube.videos().list(
            part="statistics", id=",".join(batch), maxResults=50
        ).execute()
        for item in result.get("items", []) or []:
            stats = item.get("statistics", {})
            rows.append(
                {
                    "video": item["id"],
                    "views": int(stats.get("viewCount", 0) or 0),
                    "engagedViews": None,
                    "likes": int(stats.get("likeCount", 0) or 0),
                    "comments": int(stats.get("commentCount", 0) or 0),
                    "shares": None,
                    "estimatedMinutesWatched": None,
                    "averageViewDuration": None,
                    "averageViewPercentage": None,
                    "subscribersGained": None,
                    "subscribersLost": None,
                    "data_source": "recent_statistics",
                }
            )
    return rows


def collect(now_utc=None):
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    local_today = now.astimezone(LOCAL_TZ).date()
    start_date = (local_today - timedelta(days=WINDOW_DAYS)).isoformat()
    end_date = local_today.isoformat()
    credentials = _credentials()
    aggregate = collect_aggregate(credentials, start_date, end_date)
    recent = collect_recent(credentials, now)
    return {
        "schema_version": 1,
        "captured_at": now.isoformat().replace("+00:00", "Z"),
        "window": {"start_date": start_date, "end_date": end_date},
        "aggregate": aggregate,
        "recent": recent,
        "runtime_commit_sha": os.environ.get("RUNTIME_COMMIT_SHA", ""),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/tmp/runtime-observation.json")
    args = parser.parse_args()
    payload = collect()
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Observation PASS aggregate={len(payload['aggregate'])} "
        f"recent={len(payload['recent'])}"
    )


if __name__ == "__main__":
    main()
