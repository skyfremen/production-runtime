import os
import re
from datetime import datetime, timezone

from output.state import RecoveryBlocked, check_identity, now, record_path, workflow_identity
from base.contract import (
    EXPECTED_YOUTUBE_CHANNEL_ID,
    OUTPUT_DIR,
    atomic_write_json,
    marker_tag,
    request_content_id,
)

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def expected_publication(request_data, *, require_future=True, now_utc=None):
    publication = request_data.get("publication")
    if not isinstance(publication, dict):
        raise ValueError("Scheduled publication contract is required")
    if publication.get("mode") != "scheduled":
        raise ValueError("publication.mode must be scheduled")
    raw = str(publication.get("publish_at", ""))
    if not raw.endswith("Z"):
        raise ValueError("Scheduled publish_at must be UTC RFC3339 ending Z")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid scheduled publish_at") from None
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Scheduled publish_at must be UTC")
    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    current = current.astimezone(timezone.utc)
    if require_future and parsed <= current:
        raise ValueError("Scheduled publish_at must be in the future at upload time")
    return raw


def _append_unique_tag(tags, seen, value):
    clean = str(value or "").strip().lstrip("#")
    if clean and clean.lower() not in seen:
        tags.append(clean)
        seen.add(clean.lower())


def build_upload_body(request_data, *, require_future=True, now_utc=None):
    """Build the canonical private + publishAt YouTube request body."""
    publish_at = expected_publication(
        request_data, require_future=require_future, now_utc=now_utc
    )
    marker = marker_tag(request_content_id(request_data))
    youtube = request_data["youtube"]
    description = str(youtube["description"]).strip()
    existing = {
        value.lower()
        for value in re.findall(r"(?<!\w)#[A-Za-z0-9_]+", description)
    }
    extras = []
    for hashtag in youtube["hashtags"]:
        hashtag = str(hashtag).strip()
        if hashtag and hashtag.lower() not in existing:
            extras.append(hashtag)
            existing.add(hashtag.lower())
    if extras:
        description += "\n\n" + " ".join(extras)
    if len(description.encode("utf-8")) > 5000:
        raise ValueError("Description exceeds YouTube's 5000-byte limit")

    tags = [marker]
    seen = {marker.lower()}
    for tag in youtube["tags"]:
        _append_unique_tag(tags, seen, tag)
    for hashtag in youtube["hashtags"]:
        _append_unique_tag(tags, seen, hashtag)
    tag_cost = sum(len(tag) + (2 if " " in tag else 0) for tag in tags) + max(0, len(tags) - 1)
    if tag_cost > 500:
        raise ValueError("Tags exceed YouTube's combined 500-character limit")

    return {
        "snippet": {
            "title": str(youtube["title"]),
            "description": description,
            "tags": tags,
            "categoryId": str(youtube["category_id"]),
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": bool(youtube["made_for_kids"]),
            "publishAt": publish_at,
        },
    }


def make_client():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials(
        token=None,
        refresh_token=_credential("RUNTIME_AUTH_C", "YOUTUBE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=_credential("RUNTIME_AUTH_A", "YOUTUBE_CLIENT_ID"),
        client_secret=_credential("RUNTIME_AUTH_B", "YOUTUBE_CLIENT_SECRET"),
        scopes=YOUTUBE_SCOPES,
    )
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def _credential(primary, legacy):
    value = os.environ.get(primary) or os.environ.get(legacy)
    if not value:
        raise KeyError(primary)
    return value


def authenticated_channel(youtube):
    items = youtube.channels().list(
        part="id,snippet,contentDetails", mine=True
    ).execute().get("items", [])
    if len(items) != 1:
        raise RecoveryBlocked("Exactly one authenticated YouTube channel is required")
    if items[0].get("id") != EXPECTED_YOUTUBE_CHANNEL_ID:
        raise RecoveryBlocked(
            "Credentials resolve to a different channel than the pinned production channel"
        )
    return items[0]


def find_existing_by_marker(youtube, content_id, max_videos=5000, channel=None):
    channel = channel or authenticated_channel(youtube)
    playlist = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if not playlist:
        raise RecoveryBlocked("Cannot enumerate authenticated channel uploads")

    video_ids = []
    token = None
    while True:
        response = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=playlist,
            maxResults=50,
            pageToken=token,
        ).execute()
        video_ids.extend(item["contentDetails"]["videoId"] for item in response.get("items", []))
        token = response.get("nextPageToken")
        if not token:
            break
        if len(video_ids) >= max_videos:
            raise RecoveryBlocked(
                "Upload inventory limit reached; absence cannot authorize another upload"
            )

    marker = marker_tag(content_id)
    matches = {}
    for start in range(0, len(video_ids), 50):
        response = youtube.videos().list(
            part="snippet,status",
            id=",".join(video_ids[start:start + 50]),
            maxResults=50,
        ).execute()
        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            if marker not in snippet.get("tags", []):
                continue
            if snippet.get("channelId") != channel["id"]:
                raise RecoveryBlocked("Recovery candidate channel mismatch")
            matches[item["id"]] = item
    if len(matches) > 1:
        raise RecoveryBlocked(
            "Multiple videos match this immutable content ID; operator reconciliation required"
        )
    return next(iter(matches.values()), None)


def upload_new(youtube, request_data, video_path, body):
    from googleapiclient.http import MediaFileUpload

    publish_at = expected_publication(request_data)
    status = body.get("status", {})
    if status.get("privacyStatus") != "private":
        raise RecoveryBlocked("Scheduled upload must enter YouTube as private")
    if status.get("publishAt") != publish_at:
        raise RecoveryBlocked(
            "Scheduled upload body does not match immutable publication time"
        )
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )
    response = None
    while response is None:
        _, response = request.next_chunk(num_retries=0)
    return response


def recover_record(youtube, state, identity, channel):
    path = record_path(identity["content_id"], "upload")
    stored = state.load(path)
    if stored:
        check_identity(stored.data, identity)
        return stored

    intent = state.load(record_path(identity["content_id"], "intent"))
    if not intent:
        return None
    check_identity(intent.data, identity)
    if intent.data["expected_channel_id"] != channel["id"]:
        raise RecoveryBlocked("Upload intent belongs to another authenticated channel")
    found = find_existing_by_marker(
        youtube, identity["content_id"], channel=channel
    )
    if not found:
        raise RecoveryBlocked(
            "Upload intent exists but no video is observable yet; recovery only, never re-upload"
        )
    payload = {
        **intent.data,
        "record_type": "upload",
        "youtube_video_id": found["id"],
        "uploaded_at": found["snippet"]["publishedAt"],
        "association": {
            "kind": "metadata_lookup_after_durable_intent",
            "intent_blob_sha": intent.sha,
            "observed_at": now(),
            "video_item": found,
        },
    }
    return state.create(path, payload)


def authorize_fresh_upload(youtube, state, identity, channel):
    if state.load(record_path(identity["content_id"], "intent")):
        raise RecoveryBlocked(
            "Upload intent exists; recovery must resolve it before any insert"
        )
    if find_existing_by_marker(youtube, identity["content_id"], channel=channel):
        raise RecoveryBlocked(
            "Existing untracked upload found; import its provenance instead of uploading"
        )


def execute_upload(
    request_data,
    video_path,
    *,
    state,
    identity,
    channel,
    selection,
    render_meta,
    youtube,
):
    recovered = recover_record(youtube, state, identity, channel)
    if recovered:
        return recovered, True

    body = build_upload_body(request_data)
    authorize_fresh_upload(youtube, state, identity, channel)
    intent = {
        "schema_version": 1,
        "record_type": "intent",
        **identity,
        "expected_channel_id": channel["id"],
        "created_at": now(),
        "upload_workflow": workflow_identity(),
        "background": selection,
        "render": render_meta,
        "upload_body": body,
    }
    claim = state.create(record_path(identity["content_id"], "intent"), intent)
    if not claim.created:
        raise RecoveryBlocked(
            "This attempt did not exclusively create the upload intent; insert forbidden"
        )
    try:
        response = upload_new(youtube, request_data, video_path, body)
    except Exception:
        recovered = recover_record(youtube, state, identity, channel)
        if recovered:
            return recovered, True
        raise

    atomic_write_json(OUTPUT_DIR / "upload-response.json", response)
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", str(response.get("id", ""))):
        raise RecoveryBlocked(
            "Upload response lacks a valid video ID; existing intent prevents re-upload"
        )
    payload = {
        **intent,
        "record_type": "upload",
        "youtube_video_id": response["id"],
        "uploaded_at": now(),
        "association": {
            "kind": "youtube_insert_response",
            "intent_blob_sha": claim.sha,
            "intent_commit_sha": claim.commit,
            "response": response,
        },
    }
    atomic_write_json(OUTPUT_DIR / "upload-evidence.json", payload)
    return state.create(record_path(identity["content_id"], "upload"), payload), False
