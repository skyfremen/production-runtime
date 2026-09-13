"""Receipt finalizer with schema-v7 ordered sequence evidence."""
import copy

from guard.schema import CONCATENATED_FIT_TO_SHORT_MODE, sequence_for_slot
from output import receipt_v6 as legacy6
from output import receipt_base as receipt_impl
from output.receipt_v6 import *

_legacy_v6_build_receipt = legacy6.build_receipt
_base_build_receipt = receipt_impl.build_receipt
# Compatibility hook retained for existing v5 tests/callers that patch the
# pre-v5 receipt builder through the current module facade.
_legacy_build_receipt = legacy6._legacy_build_receipt


def _sync_overrides():
    for name in (
        "workflow_identity", "GitHubState", "identity_for", "OUTPUT_DIR",
        "atomic_write_json", "ensure_request_path_matches", "load_json",
    ):
        if name in globals():
            setattr(receipt_impl, name, globals()[name])
            setattr(legacy6, name, globals()[name])
    legacy6._legacy_build_receipt = globals()["_legacy_build_receipt"]


def _same_sequence(left, right):
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
        return False
    for actual, expected in zip(left, right):
        if not isinstance(actual, dict) or not isinstance(expected, dict):
            return False
        try:
            if (
                actual.get("background_id") != expected.get("background_id")
                or abs(float(actual.get("segment_start_seconds")) - float(expected.get("segment_start_seconds"))) > 1e-6
                or abs(float(actual.get("segment_duration_seconds")) - float(expected.get("segment_duration_seconds"))) > 1e-6
            ):
                return False
        except (TypeError, ValueError):
            return False
    return True


def build_receipt(request_path, request, upload, selection, render_meta):
    _sync_overrides()
    version = request.get("schema_version")
    if version in {4, 5, 6}:
        return _legacy_v6_build_receipt(request_path, request, upload, selection, render_meta)
    if version != 7:
        raise RecoveryBlocked("Only schema-v4/v5/v6/v7 requests can produce receipts")

    slot = selection.get("background_selection")
    if slot not in {"primary", "backup"}:
        raise RecoveryBlocked("Background sequence selection slot is invalid")
    expected = sequence_for_slot(request, slot)
    executed = selection.get("background_sequence")
    if not _same_sequence(executed, expected):
        raise RecoveryBlocked("Executed background sequence differs from immutable request")

    # Reuse the proven base receipt identity/publication/render evidence checks by
    # presenting the first selected logical ID as its legacy compatibility anchor.
    legacy_request = copy.deepcopy(request)
    legacy_request["schema_version"] = 4
    legacy_request["visual"] = {
        "background_primary_id": request["visual"]["background_primary_sequence"][0]["background_id"],
        "background_backup_id": request["visual"]["background_backup_sequence"][0]["background_id"],
    }
    candidate = _base_build_receipt(
        request_path, legacy_request, upload, selection, render_meta
    )

    metrics = selection.get("metrics") or {}
    if metrics.get("background_treatment_mode") != CONCATENATED_FIT_TO_SHORT_MODE:
        raise RecoveryBlocked("Missing concatenated-fit-to-short execution evidence")
    if metrics.get("background_treatment_loop_mode") != "none" or int(metrics.get("background_treatment_loop_count", -1)) != 0:
        raise RecoveryBlocked("Schema-v7 production must record zero loops")
    try:
        derived_rate = float(metrics["background_treatment_derived_playback_rate"])
        source_seconds = float(metrics["background_sequence_source_duration_seconds"])
        output_seconds = float(metrics["background_treatment_output_duration_seconds"])
        required_seconds = float(metrics["background_treatment_required_output_seconds"])
    except (KeyError, TypeError, ValueError):
        raise RecoveryBlocked("Incomplete schema-v7 sequence timing evidence") from None
    expected_source = sum(float(x["segment_duration_seconds"]) for x in expected)
    if abs(source_seconds - expected_source) > 0.30:
        raise RecoveryBlocked("Sequence source-duration evidence differs from immutable request")
    if output_seconds + 0.30 < required_seconds:
        raise RecoveryBlocked("Concatenated background does not cover rendered timeline")

    candidate["schema_version"] = 7
    candidate["background_treatment"] = {
        "mode": CONCATENATED_FIT_TO_SHORT_MODE,
        "sequence": expected,
    }
    candidate["background_requested_primary_sequence"] = request["visual"]["background_primary_sequence"]
    candidate["background_requested_backup_sequence"] = request["visual"]["background_backup_sequence"]
    candidate["background_sequence_evidence"] = selection.get("background_sequence_evidence")
    candidate["background_usage"] = {
        "selection": slot,
        "mode": CONCATENATED_FIT_TO_SHORT_MODE,
        "logical_asset_ids": [x["background_id"] for x in expected],
        "segments": expected,
        "source_duration_seconds": source_seconds,
        "derived_playback_rate": derived_rate,
        "required_output_seconds": required_seconds,
        "treated_output_seconds": output_seconds,
        "loop_mode": "none",
        "loop_count": 0,
        "counts_for_diversity": True,
        "source": "verified_immutable_success_receipt",
    }
    return candidate


def main():
    _sync_overrides()
    receipt_impl.build_receipt = build_receipt
    return receipt_impl.main()


if __name__ == "__main__":
    main()
