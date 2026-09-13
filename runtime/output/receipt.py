"""Receipt finalizer with schema-v6 continuous-background evidence."""
import copy

from guard.schema import FIT_TO_SHORT_MODE, treatment_for_slot
from output import receipt_v5 as legacy5
from output import receipt_base as receipt_impl
from output.receipt_v5 import *

_legacy_v5_build_receipt = legacy5.build_receipt
_base_build_receipt = receipt_impl.build_receipt
# Compatibility hook retained for existing v5 tests/callers that patch the
# pre-v5 receipt builder through the current module facade.
_legacy_build_receipt = legacy5._legacy_build_receipt


def _sync_legacy_overrides():
    for name in (
        "workflow_identity",
        "GitHubState",
        "identity_for",
        "OUTPUT_DIR",
        "atomic_write_json",
        "ensure_request_path_matches",
        "load_json",
    ):
        if name in globals():
            setattr(receipt_impl, name, globals()[name])
            setattr(legacy5, name, globals()[name])
    legacy5._legacy_build_receipt = globals()["_legacy_build_receipt"]


def _same_range(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    try:
        return (
            left.get("mode") == right.get("mode") == FIT_TO_SHORT_MODE
            and abs(
                float(left.get("segment_start_seconds"))
                - float(right.get("segment_start_seconds"))
            )
            <= 1e-6
            and abs(
                float(left.get("segment_duration_seconds"))
                - float(right.get("segment_duration_seconds"))
            )
            <= 1e-6
        )
    except (TypeError, ValueError):
        return False


def build_receipt(request_path, request, upload, selection, render_meta):
    _sync_legacy_overrides()
    version = request.get("schema_version")
    if version in {4, 5}:
        return _legacy_v5_build_receipt(
            request_path,
            request,
            upload,
            selection,
            render_meta,
        )
    if version != 6:
        raise RecoveryBlocked("Only schema-v4/v5/v6 requests can produce receipts")

    legacy_request = copy.deepcopy(request)
    legacy_request["schema_version"] = 4
    legacy_request["visual"] = {
        "background_primary_id": request["visual"]["background_primary_id"],
        "background_backup_id": request["visual"]["background_backup_id"],
    }
    candidate = _base_build_receipt(
        request_path,
        legacy_request,
        upload,
        selection,
        render_meta,
    )

    slot = selection.get("background_selection")
    if slot not in {"primary", "backup"}:
        raise RecoveryBlocked("Background selection slot is invalid")
    expected = treatment_for_slot(request, slot)
    executed = selection.get("background_treatment")
    if not _same_range(executed, expected):
        raise RecoveryBlocked(
            "Executed continuous background range differs from immutable request"
        )

    metrics = selection.get("metrics") or {}
    if metrics.get("background_treatment_mode") != FIT_TO_SHORT_MODE:
        raise RecoveryBlocked("Missing fit-to-short execution evidence")
    if (
        metrics.get("background_treatment_loop_mode") != "none"
        or int(metrics.get("background_treatment_loop_count", -1)) != 0
    ):
        raise RecoveryBlocked("Normal schema-v6 production must record zero loops")
    try:
        derived_rate = float(metrics["background_treatment_derived_playback_rate"])
        output_seconds = float(metrics["background_treatment_output_duration_seconds"])
        required_seconds = float(metrics["background_treatment_required_output_seconds"])
    except (KeyError, TypeError, ValueError):
        raise RecoveryBlocked("Incomplete fit-to-short timing evidence") from None
    if output_seconds + 0.30 < required_seconds:
        raise RecoveryBlocked("Continuous background does not cover rendered timeline")

    candidate["schema_version"] = 6
    candidate["background_treatment"] = expected
    candidate["background_usage"] = {
        **candidate["background_usage"],
        "mode": FIT_TO_SHORT_MODE,
        "segment_start_seconds": expected["segment_start_seconds"],
        "segment_duration_seconds": expected["segment_duration_seconds"],
        "derived_playback_rate": derived_rate,
        "required_output_seconds": required_seconds,
        "treated_output_seconds": output_seconds,
        "loop_mode": "none",
        "loop_count": 0,
    }
    return candidate


def main():
    _sync_legacy_overrides()
    receipt_impl.build_receipt = build_receipt
    return receipt_impl.main()


if __name__ == "__main__":
    main()
