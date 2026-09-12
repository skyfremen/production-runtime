"""Receipt finalizer with schema-v5 background-treatment evidence.

The established receipt implementation is retained in receipt_base. Schema v4
continues through that code unchanged; schema v5 additionally proves that the
executed segment/playback treatment exactly matches the selected request slot.
"""

import copy

from guard.schema import treatment_for_slot
from output import receipt_base as base
from output.receipt_base import *  # re-export the established receipt surface

_legacy_build_receipt = base.build_receipt


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
    version = request.get("schema_version")
    if version == 4:
        return _legacy_build_receipt(
            request_path, request, upload, selection, render_meta
        )
    if version != 5:
        raise RecoveryBlocked("Only schema-v4/v5 requests can produce receipts")

    # Reuse all established identity/upload/verification/render invariants. The
    # legacy implementation sees only its supported version marker; request bytes,
    # immutable path identity and the selected background evidence remain the real
    # v5 values throughout verification.
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
    # receipt_base.main owns persistence/idempotency. Inject the version-aware
    # builder without duplicating any durable-state logic.
    base.build_receipt = build_receipt
    return base.main()


if __name__ == "__main__":
    main()
