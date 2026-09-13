"""Receipt finalizer with schema-v5 background-treatment evidence.

The established receipt implementation is retained in receipt_base. Schema v4
continues through that code unchanged; schema v5 additionally proves that the
executed segment/playback treatment exactly matches the selected request slot.
"""

import copy

from guard.schema import treatment_for_slot
from output import receipt_base as receipt_impl
from output.receipt_base import *  # re-export the established receipt surface

_legacy_build_receipt = receipt_impl.build_receipt


def _sync_legacy_overrides():
    """Keep the established receipt module observable/patchable through this facade."""
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


def _same_treatment(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    try:
        return (
            abs(float(left.get("segment_start_seconds")) - float(right.get("segment_start_seconds"))) <= 1e-6
            and (
                left.get("segment_duration_seconds") is None
                and right.get("segment_duration_seconds") is None
                or left.get("segment_duration_seconds") is not None
                and right.get("segment_duration_seconds") is not None
                and abs(
                    float(left.get("segment_duration_seconds"))
                    - float(right.get("segment_duration_seconds"))
                ) <= 1e-6
            )
            and abs(float(left.get("playback_rate")) - float(right.get("playback_rate"))) <= 1e-6
        )
    except (TypeError, ValueError):
        return False


def build_receipt(request_path, request, upload, selection, render_meta):
    _sync_legacy_overrides()
    version = request.get("schema_version")
    if version == 4:
        return _legacy_build_receipt(
            request_path, request, upload, selection, render_meta
        )
    if version != 5:
        raise RecoveryBlocked("Only schema-v4/v5 requests can produce receipts")

    legacy_request = copy.deepcopy(request)
    legacy_request["schema_version"] = 4
    candidate = _legacy_build_receipt(
        request_path, legacy_request, upload, selection, render_meta
    )

    slot = selection.get("background_selection")
    if slot not in {"primary", "backup"}:
        raise RecoveryBlocked("Background selection slot is invalid")
    expected = treatment_for_slot(request, slot)
    executed = selection.get("background_treatment")
    if not _same_treatment(executed, expected):
        raise RecoveryBlocked(
            "Executed background treatment differs from immutable request"
        )

    candidate["schema_version"] = 5
    candidate["background_treatment"] = expected
    candidate["background_usage"] = {
        **candidate["background_usage"],
        "segment_start_seconds": expected["segment_start_seconds"],
        "segment_duration_seconds": expected["segment_duration_seconds"],
        "playback_rate": expected["playback_rate"],
    }
    return candidate


def main():
    _sync_legacy_overrides()
    receipt_impl.build_receipt = build_receipt
    return receipt_impl.main()


if __name__ == "__main__":
    main()
