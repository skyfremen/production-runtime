#!/usr/bin/env python3
"""Collect Wacky Dramas YouTube analytics and refresh private planner context."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from analytics_reporting import sync_reporting
from analytics_retention import collect_retention

WINDOW_DAYS = 30
ANALYTICS_VERSION = 3
SGT = timezone(timedelta(hours=8))
CID_RE = re.compile(r"^wd-[0-9a-f]{24}$")
VIDEO_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
ANALYTICS_METRICS = "views,engagedViews,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained"
OPTIONAL_VIDEO_METRICS = ("dislikes", "subscribersLost", "videosAddedToPlaylists", "videosRemovedFromPlaylists")
HOOK_TYPES = {"accusation", "discovery", "contradiction", "money_stakes", "social_exposure", "urgency", "confession", "consequence_first"}
OPTIONAL_ANALYTICS_REPORTS = {
    "geography": ("country,creatorContentType", "views,engagedViews,estimatedMinutesWatched,averageViewDuration,averageViewPercentage", 250),
    "traffic_source": ("insightTrafficSourceType,creatorContentType", "views,engagedViews,estimatedMinutesWatched", 100),
    "playback_location": ("insightPlaybackLocationType,creatorContentType", "views,engagedViews,estimatedMinutesWatched", 100),
    "device_os": ("deviceType,operatingSystem,creatorContentType", "views,engagedViews,estimatedMinutesWatched", 250),
    "subscriber_status": ("subscribedStatus,creatorContentType", "views,engagedViews,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,dislikes,shares", 100),
    "demographics": ("ageGroup,gender,creatorContentType", "viewerPercentage", 100),
    "sharing": ("sharingService,creatorContentType", "shares", 100),
    "creator_content_type": ("creatorContentType", "views,engagedViews,estimatedMinutesWatched,averageViewDuration,averageViewPercentage", 20),
}


@dataclass
class AnalyticsOutputs:
    planner_files: list[Path]
    warehouse_files: list[Path]
    warnings: list[str]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return dt.astimezone(timezone.utc)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def credential(name: str) -> str:
    value = str(os.environ.get(name, "")).strip()
    if not value:
        raise RuntimeError(f"Missing required secret {name}")
    return value


def access_token() -> str:
    body = urlencode({
        "client_id": credential("RUNTIME_AUTH_A"),
        "client_secret": credential("RUNTIME_AUTH_B"),
        "refresh_token": credential("RUNTIME_AUTH_C"),
        "grant_type": "refresh_token",
    }).encode()
    req = Request("https://oauth2.googleapis.com/token", data=body, method="POST",
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=30) as response:
            data = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"YouTube OAuth refresh failed: {type(exc).__name__}") from None
    token = str(data.get("access_token", "")).strip()
    if not token:
        raise RuntimeError("YouTube OAuth refresh returned no access_token")
    return token


def get_json(url: str, token: str):
    req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urlopen(req, timeout=45) as response:
        return json.load(response)


def youtube_data(path: str, params: dict, token: str):
    return get_json("https://www.googleapis.com/youtube/v3/" + path + "?" + urlencode(params), token)


def analytics_report(params: dict, token: str):
    return get_json("https://youtubeanalytics.googleapis.com/v2/reports?" + urlencode(params), token)


def reporting_json(method: str, path: str, token: str, body=None):
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    req = Request(
        "https://youtubereporting.googleapis.com/v1/" + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=60) as response:
        return json.load(response)


def reporting_bytes(url: str, token: str) -> bytes:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    trusted = any(host == suffix or host.endswith("." + suffix) for suffix in ("googleapis.com", "googleusercontent.com"))
    if parsed.scheme != "https" or not trusted:
        raise RuntimeError("Reporting download URL host is not trusted")
    req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "text/csv"})
    with urlopen(req, timeout=120) as response:
        return response.read()


def report_rows(report: dict) -> list[dict]:
    headers = [str(h.get("name")) for h in report.get("columnHeaders", [])]
    return [dict(zip(headers, values)) for values in report.get("rows", []) or []]


def metric_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def collect_optional_analytics_reports(token: str, now: datetime, report_fn=analytics_report) -> tuple[dict, list[str]]:
    start = (now - timedelta(days=WINDOW_DAYS)).date().isoformat()
    end = now.date().isoformat()
    reports = {}
    warnings = []
    for name, (dimensions, metrics, maximum) in OPTIONAL_ANALYTICS_REPORTS.items():
        try:
            params = {
                "ids": "channel==MINE",
                "startDate": start,
                "endDate": end,
                "metrics": metrics,
                "dimensions": dimensions,
                "maxResults": maximum,
            }
            if "views" in metrics:
                params["sort"] = "-views"
            report = report_fn(params, token)
            reports[name] = {
                "dimensions": dimensions.split(","),
                "metrics": metrics.split(","),
                "rows": report_rows(report),
            }
        except Exception as exc:
            warnings.append(f"Optional YouTube Analytics report {name} unavailable: {type(exc).__name__}")
    return reports, warnings


def collect_per_video_traffic_sources(
    rows: list[dict], token: str, now: datetime, report_fn=analytics_report
) -> tuple[dict[str, dict], list[str]]:
    if not rows:
        return {}, []
    start = (now - timedelta(days=WINDOW_DAYS)).date().isoformat()
    end = now.date().isoformat()
    output: dict[str, dict] = {}
    warnings = []
    for batch_rows in chunks(rows, 200):
        ids = [str(item["youtube_video_id"]) for item in batch_rows]
        try:
            report = report_fn({
                "ids": "channel==MINE",
                "startDate": start,
                "endDate": end,
                "metrics": "views,engagedViews,estimatedMinutesWatched",
                "dimensions": "video,insightTrafficSourceType",
                "filters": "video==" + ",".join(ids),
                "maxResults": len(ids) * 20,
            }, token)
            for item in report_rows(report):
                if str(item.get("insightTrafficSourceType", "")).upper() != "SHORTS":
                    continue
                video_id = str(item.get("video") or "")
                views = metric_int(item.get("views"))
                engaged = metric_int(item.get("engagedViews"))
                output[video_id] = {
                    "shorts_source_views": views,
                    "shorts_source_engaged_views": engaged,
                    "shorts_source_engaged_view_rate_percentage": round((engaged / views) * 100, 2) if views else None,
                    "shorts_source_estimated_minutes_watched": item.get("estimatedMinutesWatched"),
                }
        except Exception as exc:
            warnings.append(f"Optional per-video Shorts traffic unavailable: {type(exc).__name__}")
    return output, warnings


def duration_seconds(value: str) -> float | None:
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", str(value or ""))
    if not m:
        return None
    h, mins, sec = m.groups()
    return round((int(h or 0) * 3600) + (int(mins or 0) * 60) + float(sec or 0), 3)


def load_creative_map(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted((root / "content" / "requests").glob("*.json")):
        try:
            doc = read_json(path)
        except Exception:
            continue
        if isinstance(doc, dict) and isinstance(doc.get("items"), list):
            items = doc["items"]
        elif isinstance(doc, dict) and CID_RE.fullmatch(str(doc.get("content_id", ""))):
            items = [doc]
        else:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("content_id", ""))
            if not CID_RE.fullmatch(cid):
                continue
            story = item.get("story") if isinstance(item.get("story"), dict) else {}
            youtube = item.get("youtube") if isinstance(item.get("youtube"), dict) else {}
            raw_aware = story.get("trend_aware")
            trend_aware = raw_aware if type(raw_aware) is bool else None
            raw_topic = story.get("trend_topic")
            trend_topic = raw_topic.strip() if trend_aware is True and isinstance(raw_topic, str) and raw_topic.strip() else None
            raw_hook_type = story.get("hook_type")
            hook_type = raw_hook_type if isinstance(raw_hook_type, str) and raw_hook_type in HOOK_TYPES else None
            out[cid] = {
                "title": str(youtube.get("title", "")),
                "premise": str(story.get("premise", "")),
                "category": str(story.get("category", "")),
                "conflict": str(story.get("conflict", "")),
                "twist": str(story.get("twist", "")),
                "hook": str(story.get("hook", "")),
                "hook_type": hook_type,
                "payoff": str(story.get("punchline", "")),
                "story_tone": str(story.get("story_tone", "")),
                "lead_gender": str(story.get("lead_gender", "")),
                "trend_aware": trend_aware,
                "trend_topic": trend_topic,
            }
    return out


def eligible_videos(root: Path, creative: dict[str, dict], now: datetime) -> list[dict]:
    cutoff = now - timedelta(days=WINDOW_DAYS)
    rows = []
    for path in sorted((root / "content" / "results").glob("wd-*.json")):
        try:
            result = read_json(path)
            cid = str(result.get("content_id", ""))
            vid = str(result.get("youtube_video_id", ""))
            published = parse_dt(str(result.get("publish_at") or result.get("published_at") or ""))
        except Exception:
            continue
        if not CID_RE.fullmatch(cid) or not VIDEO_RE.fullmatch(vid):
            continue
        if published > now or published < cutoff:
            continue
        rows.append({
            "content_id": cid,
            "youtube_video_id": vid,
            "publish_at": iso_z(published),
            "age_hours": round((now - published).total_seconds() / 3600, 2),
            "creative": creative.get(cid, {}),
        })
    return rows


def collect_data_api(rows: list[dict], token: str) -> dict[str, dict]:
    def counter(stats: dict, key: str):
        value = stats.get(key)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    out: dict[str, dict] = {}
    for batch in chunks([r["youtube_video_id"] for r in rows], 50):
        data = youtube_data("videos", {"part": "statistics,contentDetails,snippet,status", "id": ",".join(batch), "maxResults": 50}, token)
        for item in data.get("items", []):
            vid = str(item.get("id", ""))
            stats = item.get("statistics") or {}
            details = item.get("contentDetails") or {}
            snippet = item.get("snippet") or {}
            status = item.get("status") or {}
            out[vid] = {
                "views": counter(stats, "viewCount"),
                "likes": counter(stats, "likeCount"),
                "comments": counter(stats, "commentCount"),
                "duration_seconds": duration_seconds(details.get("duration")),
                "youtube_published_at": snippet.get("publishedAt"),
                "privacy_status": status.get("privacyStatus"),
            }
    return out


def collect_analytics_api(rows: list[dict], token: str, now: datetime) -> tuple[dict[str, dict], bool, list[str]]:
    if not rows:
        return {}, True, []
    out: dict[str, dict] = {}
    start = (now - timedelta(days=WINDOW_DAYS)).date().isoformat()
    end = now.date().isoformat()
    warnings = []
    try:
        for batch_rows in chunks(rows, 200):
            ids = [r["youtube_video_id"] for r in batch_rows]
            report = analytics_report({
                "ids": "channel==MINE",
                "startDate": start,
                "endDate": end,
                "metrics": ANALYTICS_METRICS,
                "dimensions": "video",
                "filters": "video==" + ",".join(ids),
                "sort": "-views",
                "maxResults": len(ids),
            }, token)
            headers = [str(h.get("name")) for h in report.get("columnHeaders", [])]
            for values in report.get("rows", []) or []:
                row = dict(zip(headers, values))
                vid = str(row.pop("video", ""))
                if vid:
                    out[vid] = row
    except Exception as exc:
        detail = "OAuth token lacks YouTube Analytics read access" if isinstance(exc, HTTPError) and exc.code in {401, 403} else "YouTube Analytics per-video report unavailable"
        return {}, False, [f"{detail}; current Data API metrics were still collected ({type(exc).__name__})."]
    for metric in OPTIONAL_VIDEO_METRICS:
        try:
            for batch_rows in chunks(rows, 200):
                ids = [r["youtube_video_id"] for r in batch_rows]
                report = analytics_report({
                    "ids": "channel==MINE",
                    "startDate": start,
                    "endDate": end,
                    "metrics": metric,
                    "dimensions": "video",
                    "filters": "video==" + ",".join(ids),
                    "maxResults": len(ids),
                }, token)
                for item in report_rows(report):
                    video_id = str(item.get("video") or "")
                    if video_id:
                        out.setdefault(video_id, {})[metric] = item.get(metric)
        except Exception as exc:
            warnings.append(f"Optional video metric {metric} unavailable: {type(exc).__name__}")
    return out, True, warnings


def snapshot(root: Path, token: str | None = None, now: datetime | None = None) -> dict:
    now = now or now_utc()
    creative = load_creative_map(root)
    rows = eligible_videos(root, creative, now)
    token = token or access_token()
    current = collect_data_api(rows, token)
    live_rows = [row for row in rows if row["youtube_video_id"] in current]
    detailed, detailed_ok, detailed_warnings = collect_analytics_api(live_rows, token, now)
    analytics_reports, report_warnings = collect_optional_analytics_reports(token, now)
    per_video_traffic, traffic_warnings = collect_per_video_traffic_sources(live_rows, token, now)
    videos = []
    for row in live_rows:
        vid = row["youtube_video_id"]
        base = current[vid]
        rich = detailed.get(vid, {})
        metrics = {
            "views": base.get("views"),
            "likes": base.get("likes"),
            "comments": base.get("comments"),
            "engaged_views": rich.get("engagedViews"),
            "average_view_duration": rich.get("averageViewDuration"),
            "average_view_percentage": rich.get("averageViewPercentage"),
            "estimated_minutes_watched": rich.get("estimatedMinutesWatched"),
            "dislikes": rich.get("dislikes"),
            "shares": rich.get("shares"),
            "subscribers_gained": rich.get("subscribersGained"),
            "subscribers_lost": rich.get("subscribersLost"),
            "videos_added_to_playlists": rich.get("videosAddedToPlaylists"),
            "videos_removed_from_playlists": rich.get("videosRemovedFromPlaylists"),
            **per_video_traffic.get(vid, {}),
        }
        videos.append({
            **row,
            "duration_seconds": base.get("duration_seconds"),
            "metrics": metrics,
        })
    warnings = [*detailed_warnings, *report_warnings, *traffic_warnings]
    return {
        "analytics_version": ANALYTICS_VERSION,
        "collected_at": iso_z(now),
        "window_days": WINDOW_DAYS,
        "detailed_analytics_available": detailed_ok,
        "analytics_reports": analytics_reports,
        "warnings": warnings,
        "videos": videos,
    }


def all_snapshots(root: Path, current: dict) -> list[dict]:
    live_cids = {
        str(item.get("content_id", ""))
        for item in current.get("videos", [])
        if isinstance(item, dict) and CID_RE.fullmatch(str(item.get("content_id", "")))
    }
    docs = []
    paths = list((root / "realtime").glob("*/*.json")) + list((root / "realtime" / "legacy").glob("analytics-*.json"))
    for path in sorted(set(paths)):
        try:
            d = read_json(path)
            if isinstance(d, dict) and isinstance(d.get("videos"), list):
                d = dict(d)
                d["videos"] = [
                    item for item in d["videos"]
                    if isinstance(item, dict) and str(item.get("content_id", "")) in live_cids
                ]
                docs.append(d)
        except Exception:
            pass
    if not any(doc.get("collected_at") == current.get("collected_at") for doc in docs):
        docs.append(current)
    return docs


def closest_checkpoint(observations: list[dict], target: float, tolerance: float = 7.0):
    candidates = [o for o in observations if abs(float(o.get("age_hours", -9999)) - target) <= tolerance]
    if not candidates:
        return None
    return min(candidates, key=lambda o: abs(float(o["age_hours"]) - target))


def med(values):
    vals = [float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(float(v))]
    if not vals:
        return None
    return round(statistics.median(vals), 2)


def duration_bucket(seconds):
    if not isinstance(seconds, (int, float)):
        return "unknown"
    if seconds < 45:
        return "under_45"
    if seconds < 55:
        return "45_54"
    if seconds < 65:
        return "55_64"
    if seconds < 75:
        return "65_74"
    return "75_plus"


def publish_time_bucket_sgt(value) -> str:
    try:
        hour = parse_dt(str(value)).astimezone(SGT).hour
    except Exception:
        return "unknown"
    start = (hour // 4) * 4
    return f"{start:02d}-{start + 3:02d}"


def publish_hour_sgt(value) -> str:
    try:
        return f"{parse_dt(str(value)).astimezone(SGT).hour:02d}"
    except Exception:
        return "unknown"


def publish_slot_sgt(value) -> str:
    try:
        local = parse_dt(str(value)).astimezone(SGT)
        return f"{local.hour:02d}:{local.minute:02d}"
    except Exception:
        return "unknown"


def performance_rows(snapshots: list[dict]) -> list[dict]:
    by_cid: dict[str, list[dict]] = {}
    for snap in snapshots:
        for item in snap.get("videos", []):
            cid = str(item.get("content_id", ""))
            if CID_RE.fullmatch(cid):
                by_cid.setdefault(cid, []).append(item)
    out = []
    for cid, obs in by_cid.items():
        obs.sort(key=lambda x: float(x.get("age_hours", 0)))
        latest = obs[-1]
        p2 = closest_checkpoint(obs, 2, tolerance=1.5)
        p6 = closest_checkpoint(obs, 6, tolerance=3)
        p24 = closest_checkpoint(obs, 24)
        p72 = closest_checkpoint(obs, 72, tolerance=12)
        p7d = closest_checkpoint(obs, 168, tolerance=24)
        creative = latest.get("creative") or {}
        lm = latest.get("metrics") or {}
        latest_age = float(latest.get("age_hours", 0) or 0)
        latest_views = lm.get("views")
        latest_engaged = lm.get("engaged_views")
        engaged_views_per_view_percentage = None
        if (
            latest_age >= 72
            and isinstance(latest_views, (int, float))
            and float(latest_views) > 0
            and isinstance(latest_engaged, (int, float))
        ):
            engaged_views_per_view_percentage = round((float(latest_engaged) / float(latest_views)) * 100, 2)
        aware = creative.get("trend_aware")
        trend_lane = "trend_aware" if aware is True else "evergreen" if aware is False else "untracked"
        hook_type = creative.get("hook_type") if creative.get("hook_type") in HOOK_TYPES else "untracked"
        shorts_views = lm.get("shorts_source_views")
        shorts_engaged = lm.get("shorts_source_engaged_views")
        shorts_rate = lm.get("shorts_source_engaged_view_rate_percentage")
        if shorts_rate is None and isinstance(shorts_views, (int, float)) and shorts_views > 0 and isinstance(shorts_engaged, (int, float)):
            shorts_rate = round((float(shorts_engaged) / float(shorts_views)) * 100, 2)
        checkpoints = {}
        for label, observation in (("2h", p2), ("6h", p6), ("24h", p24), ("72h", p72), ("7d", p7d)):
            if observation:
                checkpoints[label] = {
                    "actual_age_hours": float(observation.get("age_hours", 0)),
                    "views": (observation.get("metrics") or {}).get("views"),
                }
        out.append({
            "content_id": cid,
            "title": creative.get("title", ""),
            "premise": creative.get("premise", ""),
            "category": creative.get("category", "") or "unknown",
            "story_tone": creative.get("story_tone", "") or "unknown",
            "lead_gender": creative.get("lead_gender", "") or "unknown",
            "hook": creative.get("hook", ""),
            "hook_type": hook_type,
            "trend_lane": trend_lane,
            "trend_topic": creative.get("trend_topic") if trend_lane == "trend_aware" else None,
            "duration_bucket": duration_bucket(latest.get("duration_seconds")),
            "duration_seconds": latest.get("duration_seconds"),
            "publish_at": latest.get("publish_at"),
            "publish_time_bucket_sgt": publish_time_bucket_sgt(latest.get("publish_at")),
            "publish_hour_sgt": publish_hour_sgt(latest.get("publish_at")),
            "publish_slot_sgt": publish_slot_sgt(latest.get("publish_at")),
            "views_2h": (p2.get("metrics") or {}).get("views") if p2 else None,
            "views_6h": (p6.get("metrics") or {}).get("views") if p6 else None,
            "views_24h": (p24.get("metrics") or {}).get("views") if p24 else None,
            "views_72h": (p72.get("metrics") or {}).get("views") if p72 else None,
            "views_7d": (p7d.get("metrics") or {}).get("views") if p7d else None,
            "retention": lm.get("average_view_percentage") if latest_age >= 72 else None,
            "engaged_views_per_view_percentage": engaged_views_per_view_percentage,
            "shorts_source_views": shorts_views,
            "shorts_source_engaged_views": shorts_engaged,
            "shorts_source_engaged_view_rate_percentage": shorts_rate,
            "subscribers_gained": lm.get("subscribers_gained") if latest_age >= 72 else None,
            "shares": lm.get("shares") if latest_age >= 72 else None,
            "checkpoint_observations": checkpoints,
        })
    return out


def group_summary(rows: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get(key) or "unknown"), []).append(r)
    out = {}
    for name, items in sorted(groups.items()):
        out[name] = {
            "sample_size": len(items),
            "sample_2h": sum(v.get("views_2h") is not None for v in items),
            "median_views_2h": med([v.get("views_2h") for v in items]),
            "sample_6h": sum(v.get("views_6h") is not None for v in items),
            "median_views_6h": med([v.get("views_6h") for v in items]),
            "sample_24h": sum(v.get("views_24h") is not None for v in items),
            "median_views_24h": med([v.get("views_24h") for v in items]),
            "sample_72h": sum(v.get("views_72h") is not None for v in items),
            "median_views_72h": med([v.get("views_72h") for v in items]),
            "sample_7d": sum(v.get("views_7d") is not None for v in items),
            "median_views_7d": med([v.get("views_7d") for v in items]),
            "mature_sample": sum(v.get("retention") is not None for v in items),
            "median_average_view_percentage": med([v.get("retention") for v in items]),
            "mature_engaged_sample": sum(v.get("engaged_views_per_view_percentage") is not None for v in items),
            "median_engaged_views_per_view_percentage": med([v.get("engaged_views_per_view_percentage") for v in items]),
            "median_subscribers_gained": med([v.get("subscribers_gained") for v in items]),
            "median_shares": med([v.get("shares") for v in items]),
        }
    return out


def trend_topic_summary(rows: list[dict]) -> dict:
    tracked = [r for r in rows if r.get("trend_lane") == "trend_aware" and r.get("trend_topic")]
    return group_summary(tracked, "trend_topic")


def publication_spacing_summary(rows: list[dict]) -> dict:
    ordered = []
    for row in rows:
        try:
            ordered.append((parse_dt(str(row.get("publish_at"))), row))
        except Exception:
            continue
    ordered.sort(key=lambda item: item[0])
    comparable = []
    exact_counts: dict[str, int] = {}
    for (previous_at, _), (published_at, row) in zip(ordered, ordered[1:]):
        gap = round((published_at - previous_at).total_seconds() / 60, 2)
        if gap < 0:
            continue
        item = dict(row)
        if gap <= 20:
            bucket = "20m_or_less"
        elif gap <= 40:
            bucket = "21_40m"
        elif gap <= 60:
            bucket = "41_60m"
        else:
            bucket = "over_60m"
        item["prior_publish_gap_bucket"] = bucket
        comparable.append(item)
        exact = str(int(gap)) if gap.is_integer() else str(gap)
        exact_counts[exact] = exact_counts.get(exact, 0) + 1
    return {
        "timezone": "Asia/Singapore",
        "interpretation": "observational evidence only; publication spacing is not changed automatically",
        "bucket_performance": group_summary(comparable, "prior_publish_gap_bucket"),
        "exact_gap_minute_samples": dict(sorted(exact_counts.items(), key=lambda pair: float(pair[0]))),
    }


def compact_example(row: dict) -> dict:
    return {
        "content_id": row["content_id"],
        "title": row.get("title", ""),
        "premise": row.get("premise", ""),
        "category": row.get("category", ""),
        "story_tone": row.get("story_tone", ""),
        "hook": row.get("hook", ""),
        "hook_type": row.get("hook_type", "untracked"),
        "trend_lane": row.get("trend_lane", "untracked"),
        "trend_topic": row.get("trend_topic"),
        "duration_seconds": row.get("duration_seconds"),
        "views_2h": row.get("views_2h"),
        "views_6h": row.get("views_6h"),
        "views_24h": row.get("views_24h"),
        "views_72h": row.get("views_72h"),
        "views_7d": row.get("views_7d"),
        "average_view_percentage": row.get("retention"),
        "shorts_source_views": row.get("shorts_source_views"),
        "shorts_source_engaged_view_rate_percentage": row.get("shorts_source_engaged_view_rate_percentage"),
        "checkpoint_observations": row.get("checkpoint_observations", {}),
    }


def learning_summary(rows: list[dict]) -> dict:
    sample_2h = sum(r.get("views_2h") is not None for r in rows)
    sample_6h = sum(r.get("views_6h") is not None for r in rows)
    sample_24h = sum(r.get("views_24h") is not None for r in rows)
    sample_72h = sum(r.get("views_72h") is not None for r in rows)
    sample_7d = sum(r.get("views_7d") is not None for r in rows)
    if sample_24h < 30 or sample_72h < 15:
        stage, weight, minimum = "cold_start", "low", 5
    elif sample_24h < 100 or sample_72h < 50 or sample_7d < 20:
        stage, weight, minimum = "early_learning", "medium", 8
    else:
        stage, weight, minimum = "established", "normal", 12
    return {
        "stage": stage,
        "analytics_weight": weight,
        "minimum_pattern_sample": minimum,
        "checkpoint_samples": {
            "2h": sample_2h,
            "6h": sample_6h,
            "24h": sample_24h,
            "72h": sample_72h,
            "7d": sample_7d,
        },
    }


def planner_projection(summary: dict) -> dict:
    learning = summary.get("learning", {})
    minimum = int(learning.get("minimum_pattern_sample", 12) or 12)
    patterns = []
    for dimension, key in (
        ("category", "category_performance"),
        ("hook_type", "hook_type_performance"),
        ("story_tone", "tone_performance"),
        ("trend_lane", "trend_performance"),
    ):
        for value, metrics in (summary.get(key) or {}).items():
            if not isinstance(metrics, dict) or int(metrics.get("sample_size", 0) or 0) < minimum:
                continue
            score = next((metrics.get(median_key) for median_key, sample_key in (
                ("median_views_7d", "sample_7d"), ("median_views_72h", "sample_72h"),
                ("median_views_24h", "sample_24h"), ("median_views_6h", "sample_6h"),
            ) if metrics.get(median_key) is not None and int(metrics.get(sample_key, 0) or 0) >= minimum), None)
            if score is None:
                continue
            patterns.append({
                "dimension": dimension,
                "value": value,
                "sample_size": int(metrics.get("sample_size", 0) or 0),
                "mature_sample": int(metrics.get("mature_sample", 0) or 0),
                "evidence_median_views": score,
                "average_view_percentage": metrics.get("median_average_view_percentage"),
            })
    patterns.sort(key=lambda item: (-float(item["evidence_median_views"]), item["dimension"], item["value"]))

    windows = []
    for name, metrics in (summary.get("publish_time_performance_sgt") or {}).items():
        if not isinstance(metrics, dict) or int(metrics.get("sample_size", 0) or 0) < minimum:
            continue
        score = next((metrics.get(median_key) for median_key, sample_key in (
            ("median_views_72h", "sample_72h"), ("median_views_24h", "sample_24h"),
            ("median_views_6h", "sample_6h"),
        ) if metrics.get(median_key) is not None and int(metrics.get(sample_key, 0) or 0) >= minimum), None)
        if score is not None:
            windows.append({"window": name, "sample_size": metrics["sample_size"], "evidence_median_views": score})
    windows.sort(key=lambda item: (-float(item["evidence_median_views"]), item["window"]))

    geography = (summary.get("geography_analysis") or {}).get("top", [])[:5]
    traffic = (summary.get("traffic_source_analysis") or {}).get("top", [])[:5]
    projection = {
        "analytics_version": ANALYTICS_VERSION,
        "generated_at": summary.get("generated_at"),
        "learning": learning,
        "creative_signals": {
            "soft_evidence_only": True,
            "supported_patterns": patterns[:6],
            "weak_patterns": list(reversed(patterns[-4:])) if len(patterns) > 4 else [],
        },
        "distribution_signals": {
            "strong_publish_windows_sgt": windows[:3],
            "weak_publish_windows_sgt": list(reversed(windows[-2:])) if len(windows) > 3 else [],
        },
        "audience_signals": {
            "dominant_countries": geography,
            "dominant_traffic_sources": traffic,
        },
        "data_freshness": {
            "latest_realtime_snapshot": summary.get("generated_at"),
            "videos_analyzed": summary.get("videos_analyzed", 0),
        },
        "warnings": list(summary.get("warnings") or [])[:5],
    }
    if len((json.dumps(projection, ensure_ascii=False, sort_keys=True) + "\n").encode()) > 16000:
        projection["creative_signals"]["supported_patterns"] = projection["creative_signals"]["supported_patterns"][:3]
        projection["creative_signals"]["weak_patterns"] = projection["creative_signals"]["weak_patterns"][:2]
        projection["warnings"] = projection["warnings"][:2]
    return projection


def report_analysis(current: dict, report_name: str, dimension_keys: tuple[str, ...], limit: int = 50) -> dict:
    report = (current.get("analytics_reports") or {}).get(report_name) or {}
    if not report:
        legacy_key = {"geography": "geography", "traffic_source": "traffic_sources"}.get(report_name)
        legacy = (current.get("distribution_breakdowns") or {}).get(legacy_key) if legacy_key else None
        if isinstance(legacy, dict):
            return {
                "sample_rows": len(legacy.get("top") or []),
                "views_total": metric_int(legacy.get("views_total")),
                "top": list(legacy.get("top") or [])[:limit],
                "source_compatibility": "legacy_v2_aggregate",
            }
    rows = []
    for row in report.get("rows", []):
        if str(row.get("creatorContentType", "SHORTS")).upper() != "SHORTS":
            continue
        item = {key: row.get(key) for key in dimension_keys if row.get(key) is not None}
        for source, target in (
            ("views", "views"), ("engagedViews", "engaged_views"),
            ("estimatedMinutesWatched", "estimated_minutes_watched"),
            ("averageViewDuration", "average_view_duration"),
            ("averageViewPercentage", "average_view_percentage"),
            ("viewerPercentage", "viewer_percentage"), ("shares", "shares"),
            ("likes", "likes"), ("dislikes", "dislikes"),
        ):
            if row.get(source) is not None:
                item[target] = row[source]
        views = metric_int(row.get("views"))
        engaged = metric_int(row.get("engagedViews"))
        if views:
            item["engaged_view_rate_percentage"] = round((engaged / views) * 100, 2)
        rows.append(item)
    rows.sort(key=lambda item: (-float(item.get("views", item.get("viewer_percentage", item.get("shares", 0))) or 0), json.dumps(item, sort_keys=True)))
    total = sum(metric_int(item.get("views")) for item in rows)
    for item in rows:
        if total and item.get("views") is not None:
            item["view_share_percentage"] = round((metric_int(item["views"]) / total) * 100, 2)
    return {"sample_rows": len(rows), "views_total": total, "top": rows[:limit]}


def retention_pattern_summary(root: Path) -> dict:
    curves = []
    for path in sorted((root / "retention").glob("*/*.json")):
        try:
            payload = read_json(path)
            headers = [str(item.get("name")) for item in payload.get("column_headers", [])]
            points = [dict(zip(headers, values)) for values in payload.get("rows", [])]
            usable = [point for point in points if isinstance(point.get("elapsedVideoTimeRatio"), (int, float)) and isinstance(point.get("audienceWatchRatio"), (int, float))]
            if not usable:
                continue
            usable.sort(key=lambda point: float(point["elapsedVideoTimeRatio"]))
            def nearest(target):
                return min(usable, key=lambda point: abs(float(point["elapsedVideoTimeRatio"]) - target)).get("audienceWatchRatio")
            drops = []
            for first, second in zip(usable, usable[1:]):
                drops.append((float(first.get("audienceWatchRatio", 0)) - float(second.get("audienceWatchRatio", 0)), first["elapsedVideoTimeRatio"], second["elapsedVideoTimeRatio"]))
            largest = max(drops, default=(0, None, None))
            curves.append({
                "video_id": payload.get("video_id"), "checkpoint": payload.get("checkpoint"),
                "observed_age_hours": payload.get("observed_age_hours"),
                "opening_retention": nearest(0.05), "early_retention": nearest(0.25),
                "mid_retention": nearest(0.5), "end_retention": nearest(0.95),
                "largest_drop": round(largest[0], 4),
                "largest_drop_region": [largest[1], largest[2]],
            })
        except Exception:
            continue
    return {
        "curve_count": len(curves),
        "median_opening_retention": med([item.get("opening_retention") for item in curves]),
        "median_mid_story_retention": med([item.get("mid_retention") for item in curves]),
        "median_end_retention": med([item.get("end_retention") for item in curves]),
        "examples": curves[:20],
    }


def shorts_feed_diagnostics(rows: list[dict]) -> dict:
    distribution_values = [row.get("views_2h") for row in rows if row.get("views_2h") is not None]
    checkpoint = "2h"
    if len(distribution_values) < 5:
        distribution_values = [row.get("views_6h") for row in rows if row.get("views_6h") is not None]
        checkpoint = "6h"
    distribution_median = med(distribution_values)
    response_median = med([row.get("shorts_source_engaged_view_rate_percentage") for row in rows])
    examples = []
    counts = {}
    for row in rows:
        exposure = row.get(f"views_{checkpoint}")
        response = row.get("shorts_source_engaged_view_rate_percentage")
        if distribution_median is None or response_median is None or exposure is None or response is None:
            label = "insufficient_evidence"
        elif float(exposure) < float(distribution_median) * 0.5 and float(response) >= float(response_median):
            label = "low_distribution_strong_response"
        elif float(exposure) >= float(distribution_median) and float(response) < float(response_median):
            label = "strong_distribution_weak_response"
        elif float(exposure) >= float(distribution_median):
            label = "strong_distribution_strong_response"
        else:
            label = "low_distribution_weak_response"
        counts[label] = counts.get(label, 0) + 1
        if len(examples) < 30:
            examples.append({
                "content_id": row.get("content_id"), "title": row.get("title"),
                "classification": label, "distribution_checkpoint": checkpoint,
                "checkpoint_views": exposure, "shorts_source_views": row.get("shorts_source_views"),
                "shorts_source_engaged_view_rate_percentage": response,
                "average_view_percentage": row.get("retention"),
            })
    return {
        "terminology": "Shorts-source views; not the exact YouTube Studio 'Shown in feed' metric",
        "distribution_checkpoint": checkpoint,
        "distribution_median_views": distribution_median,
        "response_median_percentage": response_median,
        "classification_counts": counts,
        "examples": examples,
    }


def build_summary(root: Path, current: dict) -> dict:
    rows = performance_rows(all_snapshots(root, current))
    rank_key = None
    for key in ("views_7d", "views_72h", "views_24h", "views_6h", "views_2h"):
        if sum(r.get(key) is not None for r in rows) >= 5:
            rank_key = key
            break
    if rank_key is None:
        rank_key = next((key for key in ("views_7d", "views_72h", "views_24h", "views_6h", "views_2h") if any(r.get(key) is not None for r in rows)), None)
    ranked = [r for r in rows if rank_key and r.get(rank_key) is not None]
    top = sorted(ranked, key=lambda r: float(r.get(rank_key) or 0), reverse=True)[:5] if rank_key else []
    mature = [r for r in rows if r.get("retention") is not None]
    weak = sorted(mature, key=lambda r: float(r.get("retention") or 0))[:5]
    categories = sorted({r.get("category", "unknown") for r in rows if r.get("category")})
    counts = {c: sum(r.get("category") == c for r in rows) for c in categories}
    summary = {
        "analytics_version": ANALYTICS_VERSION,
        "generated_at": current["collected_at"],
        "window_days": WINDOW_DAYS,
        "videos_analyzed": len(rows),
        "detailed_analytics_available": bool(current.get("detailed_analytics_available")),
        "learning": learning_summary(rows),
        "category_performance": group_summary(rows, "category"),
        "tone_performance": group_summary(rows, "story_tone"),
        "lead_gender_performance": group_summary(rows, "lead_gender"),
        "duration_performance": group_summary(rows, "duration_bucket"),
        "hook_type_performance": group_summary(rows, "hook_type"),
        "trend_performance": group_summary(rows, "trend_lane"),
        "trend_topic_performance": trend_topic_summary(rows),
        "publish_time_performance_sgt": group_summary(rows, "publish_time_bucket_sgt"),
        "publish_hour_performance_sgt": group_summary(rows, "publish_hour_sgt"),
        "publish_slot_performance_sgt": group_summary(rows, "publish_slot_sgt"),
        "publication_spacing_performance": publication_spacing_summary(rows),
        "geography_analysis": report_analysis(current, "geography", ("country",), 50),
        "traffic_source_analysis": report_analysis(current, "traffic_source", ("insightTrafficSourceType",), 50),
        "playback_location_analysis": report_analysis(current, "playback_location", ("insightPlaybackLocationType",), 50),
        "device_operating_system_analysis": report_analysis(current, "device_os", ("deviceType", "operatingSystem"), 100),
        "subscriber_status_analysis": report_analysis(current, "subscriber_status", ("subscribedStatus",), 20),
        "demographics_analysis": report_analysis(current, "demographics", ("ageGroup", "gender"), 50),
        "sharing_analysis": report_analysis(current, "sharing", ("sharingService",), 50),
        "creator_content_type_analysis": report_analysis(current, "creator_content_type", ("creatorContentType",), 20),
        "shorts_feed_diagnostics": shorts_feed_diagnostics(rows),
        "retention_patterns": retention_pattern_summary(root),
        "top_examples": [compact_example(r) for r in top],
        "weak_retention_examples": [compact_example(r) for r in weak],
        "low_sample_categories": [{"category": c, "sample_size": counts[c]} for c in categories if counts[c] < 5],
        "warnings": list(current.get("warnings") or []),
    }
    return summary


def build_analytics_index(root: Path, summary: dict, warnings: list[str]) -> dict:
    latest_snapshots = sorted((root / "realtime").glob("*/*.json"))
    metadata_files = sorted((root / "raw").glob("**/*.metadata.json"))
    latest_reporting_date = None
    for path in metadata_files:
        try:
            value = read_json(path).get("endTime")
            if value and (latest_reporting_date is None or value > latest_reporting_date):
                latest_reporting_date = value
        except Exception:
            continue
    def exists(pattern):
        return any(root.glob(pattern))
    return {
        "analytics_repository": "skyfremen/youtube-analytics-data",
        "analytics_version": ANALYTICS_VERSION,
        "generated_at": summary.get("generated_at"),
        "available_datasets": {
            "realtime": bool(latest_snapshots),
            "basic": exists("raw/basic/**/*.csv"),
            "traffic_source": exists("raw/traffic-source/**/*.csv"),
            "playback_location": exists("raw/playback-location/**/*.csv"),
            "device_os": exists("raw/device-os/**/*.csv"),
            "demographics": exists("raw/demographics/**/*.csv"),
            "reach": exists("raw/reach-basic/**/*.csv") or exists("raw/reach-combined/**/*.csv"),
            "combined": exists("raw/combined/**/*.csv"),
            "retention": exists("retention/*/*.json"),
        },
        "latest_reporting_date": latest_reporting_date,
        "latest_realtime_snapshot": latest_snapshots[-1].relative_to(root).as_posix() if latest_snapshots else None,
        "warnings": list(warnings)[:50],
    }


def migrate_legacy_snapshots(planner_root: Path, warehouse_root: Path) -> list[Path]:
    copied = []
    target = warehouse_root / "realtime" / "legacy"
    for source in sorted((planner_root / "content" / "analytics" / "snapshots").glob("analytics-*.json")):
        destination = target / source.name
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(destination)
    return copied


def load_manifest(path: Path, default: dict) -> dict:
    try:
        value = read_json(path)
        return value if isinstance(value, dict) else dict(default)
    except (OSError, json.JSONDecodeError):
        return dict(default)


def rebuild_planner_context(root: Path) -> Path:
    subprocess.run([sys.executable, "pipeline.py", "context"], cwd=root, check=True)
    return root / "content" / "context.json"


def run(planner_root: Path, warehouse_root: Path, collected_at: datetime | None = None) -> AnalyticsOutputs:
    collected_at = collected_at or now_utc()
    token = access_token()
    current = snapshot(planner_root, token, collected_at)
    stamp = current["collected_at"].replace("-", "").replace(":", "")
    snap_path = warehouse_root / "realtime" / collected_at.date().isoformat() / f"analytics-{stamp}.json"
    write_json(snap_path, current)
    warehouse_files = [snap_path, *migrate_legacy_snapshots(planner_root, warehouse_root)]

    jobs_path = warehouse_root / "manifest" / "report-jobs.json"
    state_path = warehouse_root / "manifest" / "collection-state.json"
    schema_path = warehouse_root / "manifest" / "schema-version.json"
    jobs = load_manifest(jobs_path, {"analytics_version": ANALYTICS_VERSION, "jobs": {}, "report_types": {}})
    state = load_manifest(state_path, {"analytics_version": ANALYTICS_VERSION, "downloaded_reports": {}, "retention_checkpoints": {}})
    warnings = list(current.get("warnings") or [])

    reporting = sync_reporting(
        token, warehouse_root, jobs, state,
        lambda method, path, body=None: reporting_json(method, path, token, body),
        lambda url: reporting_bytes(url, token), collected_at,
    )
    warehouse_files.extend(reporting.files)
    warnings.extend(reporting.warnings)

    retention = collect_retention(
        current.get("videos", []), state, warehouse_root,
        lambda params: analytics_report(params, token), collected_at,
    )
    warehouse_files.extend(retention.files)
    warnings.extend(retention.warnings)
    state["analytics_version"] = ANALYTICS_VERSION
    state["latest_realtime_snapshot"] = snap_path.relative_to(warehouse_root).as_posix()
    state["updated_at"] = current["collected_at"]
    write_json(jobs_path, jobs)
    write_json(state_path, state)
    write_json(schema_path, {
        "analytics_version": ANALYTICS_VERSION,
        "schema": "wacky-dramas-youtube-analytics",
        "updated_at": current["collected_at"],
    })
    warehouse_files.extend((jobs_path, state_path, schema_path))

    current["warnings"] = warnings
    summary = build_summary(warehouse_root, current)
    projection = planner_projection(summary)
    index = build_analytics_index(warehouse_root, summary, warnings)
    summary_path = planner_root / "content" / "analytics-summary.json"
    projection_path = planner_root / "content" / "planner-analytics.json"
    index_path = planner_root / "content" / "analytics-index.json"
    write_json(summary_path, summary)
    write_json(projection_path, projection)
    write_json(index_path, index)
    context_path = rebuild_planner_context(planner_root)
    print(f"Analytics PASS videos={len(current['videos'])} detailed={current['detailed_analytics_available']}")
    for warning in warnings:
        print(f"::warning::{warning}")
    return AnalyticsOutputs(
        planner_files=[summary_path, projection_path, index_path, context_path],
        warehouse_files=sorted(set(warehouse_files)),
        warnings=warnings,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--planner-dir", required=True)
    ap.add_argument("--warehouse-dir", required=True)
    args = ap.parse_args()
    try:
        run(Path(args.planner_dir).resolve(), Path(args.warehouse_dir).resolve())
    except Exception as exc:
        print(f"::error::ANALYTICS_FAILED {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
