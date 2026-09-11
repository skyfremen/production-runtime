import os
import re
from datetime import datetime, timedelta, timezone

from output.state import (
    RecoveryBlocked,
    check_identity,
    index_bootstrap_path,
    index_path,
    now,
    record_path,
    workflow_identity,
)
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
INTENT_RECOVERY_MAX_VIDEOS = 250
INTENT_RECOVERY_CLOCK_SKEW = timedelta(minutes=5)
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def expected_publication(request_data, *, require_future=True, now_utc=None):
    publication = request_data.get("publication")
    if not isinstance(publication, dict):
        raise ValueError("Publication contract is required")
    mode = publication.get("mode")
    if mode == "immediate":
        if publication.get("publish_at") is not None:
            raise ValueError("Immediate publication requires publish_at=null")
        return None
    if mode != "scheduled":
        raise ValueError("publication.mode must be scheduled or immediate")
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
    """Build the canonical scheduled or immediate-public YouTube request body."""
    publish_at = expected_publication(
        request_data, require_future=require_future, now_utc=now_utc
    )
    mode = request_data["publication"]["mode"]
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

    status = {
        "privacyStatus": "public" if mode == "immediate" else "private",
        "selfDeclaredMadeForKids": bool(youtube["made_for_kids"]),
    }
    if mode == "scheduled":
        status["publishAt"] = publish_at

    return {
        "snippet": {
            "title": str(youtube["title"]),
            "description": description,
            "tags": tags,
            "categoryId": str(youtube["category_id"]),
        },
        "status": status,
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


def _mapping(identity, video_id, channel_id):
    if not VIDEO_ID.fullmatch(str(video_id or "")):
        raise RecoveryBlocked("Mapping requires a valid remote video ID")
    if not channel_id:
        raise RecoveryBlocked("Mapping requires the authenticated channel identity")
    return {
        "schema_version": 1,
        "record_type": "mapping",
        **identity,
        "youtube_video_id": str(video_id),
        "expected_channel_id": str(channel_id),
    }


def load_mapping(state, identity, channel):
    stored = state.load(index_path(identity["content_id"]))
    if not stored:
        return None
    check_identity(stored.data, identity)
    if stored.data.get("record_type") != "mapping" or stored.data.get("schema_version") != 1:
        raise RecoveryBlocked("Invalid reconciliation mapping schema")
    if not VIDEO_ID.fullmatch(str(stored.data.get("youtube_video_id", ""))):
        raise RecoveryBlocked("Reconciliation mapping has an invalid video ID")
    if stored.data.get("expected_channel_id") != channel["id"]:
        raise RecoveryBlocked("Reconciliation mapping belongs to another channel")
    return stored


def ensure_mapping(state, identity, upload_record, channel):
    check_identity(upload_record, identity)
    if upload_record.get("record_type") != "upload":
        raise RecoveryBlocked("Only durable upload evidence can populate the mapping")
    if upload_record.get("expected_channel_id") != channel["id"]:
        raise RecoveryBlocked("Upload evidence belongs to another channel")
    candidate = _mapping(
        identity,
        upload_record.get("youtube_video_id"),
        upload_record.get("expected_channel_id"),
    )
    stored = state.create(index_path(identity["content_id"]), candidate)
    if stored.data != candidate:
        raise RecoveryBlocked("Reconciliation mapping conflicts with durable upload evidence")
    return stored


def require_index_ready(state):
    stored = state.load(index_bootstrap_path())
    if not stored:
        raise RecoveryBlocked("Reconciliation index bootstrap is missing; fresh upload forbidden")
    data = stored.data
    if (
        data.get("schema_version") != 1
        or data.get("status") != "complete"
        or data.get("conflicts") != 0
    ):
        raise RecoveryBlocked("Reconciliation index bootstrap is not complete")
    return stored


def verify_mapped_video(youtube, mapping, identity, channel):
    check_identity(mapping, identity)
    video_id = str(mapping.get("youtube_video_id", ""))
    response = youtube.videos().list(
        part="snippet,status", id=video_id, maxResults=1
    ).execute()
    items = response.get("items", [])
    if len(items) != 1 or items[0].get("id") != video_id:
        raise RecoveryBlocked("Indexed remote video is unavailable; fresh upload forbidden")
    item = items[0]
    snippet = item.get("snippet", {})
    if snippet.get("channelId") != channel["id"]:
        raise RecoveryBlocked("Indexed remote video belongs to another channel")
    if marker_tag(identity["content_id"]) not in snippet.get("tags", []):
        raise RecoveryBlocked("Indexed remote video marker does not match immutable identity")
    return item


def _instant(raw):
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _validate_intent_candidate(item, intent, identity, channel):
    snippet = item.get("snippet", {})
    if marker_tag(identity["content_id"]) not in snippet.get("tags", []):
        return False
    if snippet.get("channelId") != channel["id"]:
        raise RecoveryBlocked("Recovery candidate channel mismatch")
    expected = intent.get("upload_body", {}).get("snippet", {})
    for field in ("title", "description", "categoryId"):
        if snippet.get(field) != expected.get(field):
            raise RecoveryBlocked(
                f"Recovery candidate {field} differs from durable upload intent"
            )
    return True


def find_after_intent(
    youtube,
    intent,
    identity,
    *,
    channel,
    max_videos=INTENT_RECOVERY_MAX_VIDEOS,
):
    """Exceptional recovery for the crash window after the remote side effect.

    The upload intent timestamp bounds the search to recent playlist pages. Reaching
    the explicit cap never proves absence; it leaves the intent fence in force.
    """
    playlist = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if not playlist:
        raise RecoveryBlocked("Cannot enumerate authenticated channel uploads")
    created_at = _instant(intent.get("created_at"))
    if created_at is None:
        raise RecoveryBlocked("Upload intent has an invalid creation timestamp")
    cutoff = created_at - INTENT_RECOVERY_CLOCK_SKEW

    token = None
    scanned = 0
    matches = {}
    while True:
        remaining = max_videos - scanned
        if remaining <= 0:
            raise RecoveryBlocked(
                "Bounded post-intent reconciliation exhausted; intent remains authoritative"
            )
        response = youtube.playlistItems().list(
            part="contentDetails,snippet",
            playlistId=playlist,
            maxResults=min(50, remaining),
            pageToken=token,
        ).execute()
        rows = response.get("items", [])
        ids = [
            item.get("contentDetails", {}).get("videoId")
            for item in rows
            if item.get("contentDetails", {}).get("videoId")
        ]
        scanned += len(ids)
        if ids:
            videos = youtube.videos().list(
                part="snippet,status",
                id=",".join(ids),
                maxResults=len(ids),
            ).execute().get("items", [])
            for item in videos:
                if _validate_intent_candidate(item, intent, identity, channel):
                    matches[item["id"]] = item
            if len(matches) > 1:
                raise RecoveryBlocked(
                    "Multiple videos match this immutable content ID; operator reconciliation required"
                )
            if matches:
                return next(iter(matches.values()))

        page_times = []
        for row in rows:
            raw = (
                row.get("contentDetails", {}).get("videoPublishedAt")
                or row.get("snippet", {}).get("publishedAt")
            )
            parsed = _instant(raw)
            if parsed is not None:
                page_times.append(parsed)
        if page_times and min(page_times) < cutoff:
            return None

        token = response.get("nextPageToken")
        if not token:
            return None
        if scanned >= max_videos:
            raise RecoveryBlocked(
                "Bounded post-intent reconciliation exhausted; intent remains authoritative"
            )


def upload_new(youtube, request_data, video_path, body):
    from googleapiclient.http import MediaFileUpload

    publish_at = expected_publication(request_data)
    mode = request_data.get("publication", {}).get("mode")
    status = body.get("status", {})
    if mode == "scheduled":
        if status.get("privacyStatus") != "private":
            raise RecoveryBlocked("Scheduled upload must enter YouTube as private")
        if status.get("publishAt") != publish_at:
            raise RecoveryBlocked(
                "Scheduled upload body does not match immutable publication time"
            )
    elif mode == "immediate":
        if status.get("privacyStatus") != "public":
            raise RecoveryBlocked("Immediate upload must enter YouTube as public")
        if "publishAt" in status:
            raise RecoveryBlocked("Immediate upload body must not contain publishAt")
    else:
        raise RecoveryBlocked("Unsupported publication mode")
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
    mapping = load_mapping(state, identity, channel)
    stored = state.load(path)
    if stored:
        check_identity(stored.data, identity)
        if stored.data.get("expected_channel_id") != channel["id"]:
            raise RecoveryBlocked("Durable upload evidence belongs to another channel")
        if mapping and mapping.data.get("youtube_video_id") != stored.data.get("youtube_video_id"):
            raise RecoveryBlocked("Reconciliation mapping conflicts with durable upload evidence")
        if not mapping:
            ensure_mapping(state, identity, stored.data, channel)
        return stored

    if mapping:
        verify_mapped_video(youtube, mapping.data, identity, channel)
        raise RecoveryBlocked(
            "Indexed upload exists but immutable upload evidence is missing; fresh upload forbidden"
        )

    intent = state.load(record_path(identity["content_id"], "intent"))
    if not intent:
        return None
    check_identity(intent.data, identity)
    if intent.data["expected_channel_id"] != channel["id"]:
        raise RecoveryBlocked("Upload intent belongs to another authenticated channel")
    found = find_after_intent(
        youtube,
        intent.data,
        identity,
        channel=channel,
    )
    if not found:
        raise RecoveryBlocked(
            "Upload intent exists but no video is observable in its bounded recovery window; recovery only, never re-upload"
        )
    payload = {
        **intent.data,
        "record_type": "upload",
        "youtube_video_id": found["id"],
        "uploaded_at": found["snippet"]["publishedAt"],
        "association": {
            "kind": "bounded_metadata_lookup_after_durable_intent",
            "intent_blob_sha": intent.sha,
            "observed_at": now(),
            "video_item": found,
        },
    }
    recovered = state.create(path, payload)
    ensure_mapping(state, identity, recovered.data, channel)
    return recovered


def authorize_fresh_upload(youtube, state, identity, channel):
    require_index_ready(state)
    if state.load(record_path(identity["content_id"], "intent")):
        raise RecoveryBlocked(
            "Upload intent exists; recovery must resolve it before any insert"
        )
    stored = state.load(record_path(identity["content_id"], "upload"))
    if stored:
        check_identity(stored.data, identity)
        raise RecoveryBlocked("Durable upload evidence exists; recover it before any insert")
    mapping = load_mapping(state, identity, channel)
    if mapping:
        verify_mapped_video(youtube, mapping.data, identity, channel)
        raise RecoveryBlocked("Indexed upload exists; fresh insert forbidden")


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
    if not VIDEO_ID.fullmatch(str(response.get("id", ""))):
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
    stored = state.create(record_path(identity["content_id"], "upload"), payload)
    ensure_mapping(state, identity, stored.data, channel)
    return stored, False
