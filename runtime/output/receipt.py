import argparse
from datetime import datetime
from pathlib import Path

from output.state import (
    GitHubState,
    RecoveryBlocked,
    blob_sha,
    check_identity,
    identity_for,
    now,
    receipt_path,
    workflow_identity,
)
from base.contract import OUTPUT_DIR, atomic_write_json, ensure_request_path_matches, load_json

ALLOWED_VERIFICATION_STATES = {"verified_scheduled", "verified_scheduled_published"}


def _scheduled_publish_at(request, evidence):
    publication = request.get("publication")
    if not isinstance(publication, dict) or publication.get("mode") != "scheduled":
        raise RecoveryBlocked("Scheduled immutable publication contract is required")
    publish_at = publication.get("publish_at")
    if not publish_at:
        raise RecoveryBlocked("Scheduled publish_at is required")
    if evidence.get("upload_body", {}).get("status", {}).get("publishAt") != publish_at:
        raise RecoveryBlocked(
            "Durable upload evidence differs from immutable scheduled publication"
        )
    return publish_at


def build_receipt(request_path, request, upload, selection, render_meta):
    content_id = ensure_request_path_matches(request_path, request)
    schema_version = request.get("schema_version")
    if schema_version not in {3, 4}:
        raise RecoveryBlocked("Only schema-v3/v4 requests can produce receipts")

    identity = {
        key: upload.get(key)
        for key in ("content_id", "request_path", "request_blob_sha", "source_commit_sha")
    }
    if (
        identity["content_id"] != content_id
        or identity["request_blob_sha"] != blob_sha(Path(request_path).read_bytes())
    ):
        raise RecoveryBlocked("Receipt request identity does not match actual immutable bytes")

    verification = upload.get("verification", {})
    check_identity(verification, identity)
    evidence = upload.get("upload_evidence", {})
    check_identity(evidence, identity)
    publish_at = _scheduled_publish_at(request, evidence)

    if (
        verification.get("passed") is not True
        or verification.get("state") not in ALLOWED_VERIFICATION_STATES
    ):
        raise RecoveryBlocked(
            "Cannot finalize without successful scheduled YouTube verification"
        )
    if verification.get("publish_at") != publish_at:
        raise RecoveryBlocked(
            "Cannot finalize: verified scheduled publication differs from immutable request"
        )
    if verification.get("privacy_status") not in {"private", "public"}:
        raise RecoveryBlocked("Cannot finalize: scheduled video has unexpected privacy state")
    if (
        verification.get("youtube_video_id") != upload.get("youtube_video_id")
        or evidence.get("youtube_video_id") != upload.get("youtube_video_id")
    ):
        raise RecoveryBlocked("Receipt video ID does not match verified durable evidence")
    if verification.get("channel_id") != evidence.get("expected_channel_id"):
        raise RecoveryBlocked("Receipt channel identity mismatch")
    if not verification.get("verified_at") or not upload.get("recovery_record_blob_sha"):
        raise RecoveryBlocked("Verification timestamp and durable evidence SHA are mandatory")

    if render_meta.get("render_verified") is not True or render_meta.get("content_id") != content_id:
        raise RecoveryBlocked("A verified render for this content ID is required")
    render_contract = (
        ("resolution", "1080x1920"),
        ("fps", 30),
        ("video_codec", "h264"),
        ("audio_codec", "aac"),
        ("audio_stream_count", 1),
        ("h264_profile", "High"),
        ("pixel_format", "yuv420p"),
        ("color_space", "bt709"),
        ("audio_sample_rate", 48000),
        ("narration_engine", "kokoro"),
        ("narration_voice", request["narration"]["voice"]),
        ("narration_speed", request["narration"]["speed"]),
    )
    for key, expected in render_contract:
        if render_meta.get(key) != expected:
            raise RecoveryBlocked(f"Invalid verified render field: {key}")
    if render_meta != evidence.get("render") or selection != evidence.get("background"):
        raise RecoveryBlocked(
            "Receipt render/background differs from original upload evidence"
        )

    selection_slot = selection.get("background_selection")
    expected_background_key = f"background_{selection_slot}_id"
    if (
        selection_slot not in {"primary", "backup"}
        or selection.get("background_asset_id") != request["visual"].get(expected_background_key)
    ):
        raise RecoveryBlocked("Selected background is not the recorded request primary/backup")

    workflow = workflow_identity()
    origin = evidence["upload_workflow"]
    for key in ("name", "run_id", "run_attempt", "code_commit_sha"):
        if not workflow.get(key) or not origin.get(key):
            raise RecoveryBlocked(
                "Complete original and verification workflow provenance is required"
            )

    receipt_created_at = now()
    resolution_started_at = selection.get("metrics", {}).get("resolution_started_at")
    try:
        total_production_seconds = round(
            (
                datetime.fromisoformat(receipt_created_at.replace("Z", "+00:00"))
                - datetime.fromisoformat(
                    str(resolution_started_at).replace("Z", "+00:00")
                )
            ).total_seconds(),
            6,
        )
    except (TypeError, ValueError):
        total_production_seconds = None

    return {
        "schema_version": schema_version,
        **identity,
        "youtube_video_id": upload["youtube_video_id"],
        "youtube_url": upload["youtube_url"],
        "youtube_channel_id": verification["channel_id"],
        "publication_mode": "scheduled",
        "privacy_status": verification["privacy_status"],
        "publish_at": publish_at,
        "publish_at_absent": verification["publish_at_absent"],
        "verification_state": verification["state"],
        "verification": verification,
        "planning": request["planning"],
        "background_requested_primary_id": request["visual"]["background_primary_id"],
        "background_requested_backup_id": request["visual"]["background_backup_id"],
        "background_asset_id": selection["background_asset_id"],
        "background_selection": selection_slot,
        "background_usage": {
            "logical_asset_id": selection["background_asset_id"],
            "selection": selection_slot,
            "counts_for_diversity": True,
            "source": "verified_immutable_success_receipt",
        },
        "background_rendition": selection.get("rendition"),
        "background_target": selection.get("target"),
        "background_rendition_fallback_used": selection.get(
            "rendition_fallback_used", False
        ),
        "background_logical_fallback_used": selection.get(
            "logical_fallback_used", selection_slot == "backup"
        ),
        "background_generic_source_fallback_used": selection.get(
            "generic_source_fallback_used", False
        ),
        "narration_engine": render_meta["narration_engine"],
        "narration_voice": render_meta["narration_voice"],
        "narration_speed": render_meta["narration_speed"],
        "lead_gender": request["story"].get("lead_gender"),
        "story_tone": request["story"].get("story_tone"),
        "narration_seconds": render_meta["narration_seconds"],
        "video_seconds": render_meta["video_seconds"],
        "resolution": render_meta["resolution"],
        "fps": render_meta["fps"],
        "video_codec": render_meta["video_codec"],
        "audio_codec": render_meta["audio_codec"],
        "audio_stream_count": render_meta["audio_stream_count"],
        "h264_profile": render_meta["h264_profile"],
        "pixel_format": render_meta["pixel_format"],
        "color_space": render_meta["color_space"],
        "audio_sample_rate": render_meta["audio_sample_rate"],
        "background_readability_protection_applied": render_meta.get(
            "background_readability_protection_applied"
        ),
        "render_verification": render_meta,
        "renderer_source_commit": origin["code_commit_sha"],
        "upload_workflow": origin,
        "workflow_name": workflow["name"],
        "workflow_run_id": workflow["run_id"],
        "workflow_run_attempt": workflow["run_attempt"],
        "verification_source_commit": workflow["code_commit_sha"],
        "uploaded_at": upload["uploaded_at"],
        "youtube_verified_at": verification["verified_at"],
        "receipt_created_at": receipt_created_at,
        "production_metrics": {
            **selection.get("metrics", {}),
            "ffmpeg_duration_seconds": render_meta.get("ffmpeg_duration_seconds"),
            "x264_preset": render_meta.get("x264_preset"),
            "x264_crf": render_meta.get("x264_crf"),
            "tts_generation_duration_seconds": render_meta.get(
                "tts_generation_duration_seconds"
            ),
            "caption_alignment_duration_seconds": render_meta.get(
                "caption_alignment_duration_seconds"
            ),
            "render_process_duration_seconds": render_meta.get(
                "render_process_duration_seconds"
            ),
            "production_elapsed_through_render_seconds": render_meta.get(
                "production_elapsed_through_render_seconds"
            ),
            "total_production_duration_seconds": total_production_seconds,
        },
        "test_mode": render_meta["test_mode"],
        "recovery": {
            "record_path": upload["recovery_record_path"],
            "record_blob_sha": upload["recovery_record_blob_sha"],
            "recovered": upload["recovered"],
            "association": evidence["association"],
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()

    request = load_json(args.request)
    identity = identity_for(args.request, request)
    upload = load_json(OUTPUT_DIR / "upload_result.json")
    check_identity(upload, identity)
    candidate = build_receipt(
        args.request,
        request,
        upload,
        load_json(OUTPUT_DIR / "background_selection.json"),
        load_json(OUTPUT_DIR / "render-metadata.json"),
    )

    state = GitHubState()
    path = receipt_path(identity["content_id"])
    existing = state.load(path)
    if existing:
        check_identity(existing.data, identity)
        if (
            existing.data.get("schema_version") != candidate["schema_version"]
            or existing.data.get("verification", {}).get("passed") is not True
        ):
            raise RecoveryBlocked("Existing receipt is incomplete or invalid")
        for key in (
            "youtube_video_id",
            "publication_mode",
            "privacy_status",
            "publish_at",
            "publish_at_absent",
            "verification_state",
            "recovery",
        ):
            if key == "recovery":
                if (
                    existing.data[key]["record_blob_sha"]
                    != candidate[key]["record_blob_sha"]
                ):
                    raise RecoveryBlocked(
                        "Immutable receipt recovery evidence differs"
                    )
            elif existing.data.get(key) != candidate.get(key):
                raise RecoveryBlocked(f"Immutable receipt differs: {key}")
        stored = existing
        print(
            f"Immutable receipt reused unchanged: {path}; "
            f"video={candidate['youtube_video_id']}; blob={stored.sha}; no upload"
        )
    else:
        stored = state.create(path, candidate)
        print(
            f"Verified immutable receipt committed: {path}; "
            f"commit={stored.commit}; blob={stored.sha}"
        )

    atomic_write_json(
        OUTPUT_DIR / "receipt-persistence.json",
        {
            "path": path,
            "blob_sha": stored.sha,
            "commit_sha": stored.commit,
            "created": stored.created,
            "youtube_video_id": candidate["youtube_video_id"],
        },
    )


if __name__ == "__main__":
    main()
