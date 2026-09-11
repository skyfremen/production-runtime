import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

from output.state import RecoveryBlocked, check_identity, identity_for, now
from output.transfer import authenticated_channel, make_client
from base.contract import OUTPUT_DIR, atomic_write_json, load_json, marker_tag
from errors import E_VERIFY

RETRY_DELAYS = (0, 2, 4, 8, 8, 4, 4, 10, 10, 10)
FAILURE_CLASSIFICATION = Path("/tmp/runtime-failure-classification.json")


class VerificationPending(RuntimeError):
    """YouTube state is not ready yet, but durable upload evidence remains valid."""


def _instant(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _same_instant(left, right):
    return _instant(left) is not None and _instant(left) == _instant(right)


def _expected_publication(request, evidence):
    publication = request.get("publication")
    if not isinstance(publication, dict):
        raise RecoveryBlocked("Immutable publication contract is required")
    mode = publication.get("mode")
    intent_status = evidence.get("upload_body", {}).get("status", {})
    if mode == "immediate":
        if publication.get("publish_at") is not None:
            raise RecoveryBlocked("Immediate immutable publication requires publish_at=null")
        if intent_status.get("privacyStatus") != "public" or "publishAt" in intent_status:
            raise RecoveryBlocked(
                "Durable upload intent does not contain the immutable immediate-public contract"
            )
        return {"mode": "immediate", "publish_at": None}
    if mode != "scheduled":
        raise RecoveryBlocked("Unsupported immutable publication mode")
    publish_at = str(publication.get("publish_at", ""))
    if _instant(publish_at) is None:
        raise RecoveryBlocked("Invalid immutable scheduled publish_at")
    intent_publish_at = intent_status.get("publishAt")
    if intent_status.get("privacyStatus") != "private" or not _same_instant(
        intent_publish_at, publish_at
    ):
        raise RecoveryBlocked(
            "Durable upload intent does not contain the immutable scheduled publication time"
        )
    return {"mode": "scheduled", "publish_at": publish_at}


def verify_video(youtube, request, identity, evidence, sleep=time.sleep):
    check_identity(evidence, identity)
    video_id = evidence.get("youtube_video_id")
    if (
        not video_id
        or evidence.get("record_type") != "upload"
        or not evidence.get("association")
    ):
        raise RecoveryBlocked("A durable upload record is required before verification")

    channel = authenticated_channel(youtube)
    if channel["id"] != evidence.get("expected_channel_id"):
        raise RecoveryBlocked("Authenticated channel differs from upload evidence")
    expected_snippet = evidence["upload_body"]["snippet"]
    publication = _expected_publication(request, evidence)
    mode = publication["mode"]
    expected_publish_at = publication["publish_at"]
    expected_dt = _instant(expected_publish_at)

    last = "video not visible"
    observations = []
    for attempt, delay in enumerate(RETRY_DELAYS, 1):
        if delay:
            sleep(delay)
        response = youtube.videos().list(
            part="snippet,status,contentDetails", id=video_id
        ).execute()
        items = response.get("items", [])
        if not items:
            last = "video not visible"
            observations.append(
                {"attempt": attempt, "observed_at": now(), "state": last}
            )
            continue
        if len(items) != 1 or items[0].get("id") != video_id:
            raise RecoveryBlocked("YouTube returned a different video ID")

        item = items[0]
        snippet = item.get("snippet", {})
        status = item.get("status", {})
        if not snippet or not status:
            last = "snippet/status not propagated"
            observations.append(
                {"attempt": attempt, "observed_at": now(), "state": last}
            )
            continue
        if snippet.get("channelId") != channel["id"]:
            raise RecoveryBlocked("Video belongs to a different channel")
        if (
            status.get("uploadStatus") in {"failed", "rejected", "deleted"}
            or status.get("failureReason")
            or status.get("rejectionReason")
        ):
            raise RecoveryBlocked("YouTube rejected or failed the upload")
        for field in ("title", "description", "categoryId"):
            if snippet.get(field) != expected_snippet.get(field):
                raise RecoveryBlocked(
                    f"YouTube {field} differs from recorded upload metadata"
                )
        if status.get("uploadStatus") != "processed":
            last = "YouTube processing not complete"
            observations.append(
                {"attempt": attempt, "observed_at": now(), "state": last}
            )
            continue

        privacy = status.get("privacyStatus")
        remote_publish_at = status.get("publishAt")
        publish_at_absent = "publishAt" not in status
        if mode == "immediate":
            if privacy != "public":
                raise RecoveryBlocked("Immediate video is not public after processing")
            if not publish_at_absent:
                raise RecoveryBlocked("Immediate public video unexpectedly exposes publishAt")
            published_at = snippet.get("publishedAt")
            if _instant(published_at) is None:
                raise RecoveryBlocked("Immediate public video has no valid publishedAt")
            verification_state = "verified_immediate_public"
            verified_publish_at = published_at
        elif privacy == "private":
            if not _same_instant(remote_publish_at, expected_publish_at):
                raise RecoveryBlocked(
                    "YouTube scheduled publication differs from immutable request"
                )
            verification_state = "verified_scheduled"
            publish_at_absent = False
            verified_publish_at = expected_publish_at
        elif privacy == "public":
            current = datetime.now(timezone.utc)
            if expected_dt is None or current < expected_dt:
                raise RecoveryBlocked(
                    "Scheduled video became public before its immutable publication time"
                )
            if remote_publish_at is not None and not _same_instant(
                remote_publish_at, expected_publish_at
            ):
                raise RecoveryBlocked(
                    "Published video exposes a different publishAt than requested"
                )
            published_at = _instant(snippet.get("publishedAt"))
            if published_at and published_at < expected_dt:
                raise RecoveryBlocked(
                    "YouTube publishedAt predates the immutable scheduled time"
                )
            verification_state = "verified_scheduled_published"
            verified_publish_at = expected_publish_at
        else:
            raise RecoveryBlocked("Scheduled video has an unexpected privacy state")

        marker = marker_tag(identity["content_id"])
        observed_marker_tags = [marker] if marker in snippet.get("tags", []) else []
        return {
            "passed": True,
            "state": verification_state,
            "verified_at": now(),
            **identity,
            "youtube_video_id": video_id,
            "channel_id": channel["id"],
            "channel_title": channel.get("snippet", {}).get("title"),
            "channel_handle": channel.get("snippet", {}).get("customUrl"),
            "publication_mode": mode,
            "privacy_status": privacy,
            "publish_at": verified_publish_at,
            "publish_at_absent": publish_at_absent,
            "upload_status": status["uploadStatus"],
            "association_method": "immutable_github_upload_record",
            "observed_marker_tags": observed_marker_tags,
            "attempts": attempt,
            "prior_observations": observations,
        }
    raise VerificationPending(
        f"YouTube verification incomplete after {len(RETRY_DELAYS)} bounded attempts: {last}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()

    request = load_json(args.request)
    identity = identity_for(args.request, request)
    path = OUTPUT_DIR / "upload_result.json"
    upload = load_json(path)
    check_identity(upload, identity)
    verification = verify_video(
        make_client(), request, identity, upload["upload_evidence"]
    )
    upload.update(
        {
            "publication_mode": verification["publication_mode"],
            "privacy_status": verification["privacy_status"],
            "publish_at": verification["publish_at"],
            "publish_at_absent": verification["publish_at_absent"],
            "youtube_verified_at": verification["verified_at"],
            "verification": verification,
            "content_id_tag_verified": bool(verification["observed_marker_tags"]),
        }
    )
    atomic_write_json(path, upload)
    atomic_write_json(OUTPUT_DIR / "youtube-verification.json", verification)
    print(
        f"YouTube verified: {upload['youtube_video_id']}; "
        f"state={verification['state']}; publishAt={verification['publish_at']}; "
        f"tags={verification['observed_marker_tags']}"
    )


def guarded_main():
    try:
        main()
    except RecoveryBlocked:
        atomic_write_json(FAILURE_CLASSIFICATION, {
            "stage": "verify",
            "error_code": E_VERIFY,
            "retryable": False,
        })
        raise
    except Exception:
        atomic_write_json(FAILURE_CLASSIFICATION, {
            "stage": "verify",
            "error_code": E_VERIFY,
            "retryable": True,
        })
        raise


if __name__ == "__main__":
    guarded_main()
