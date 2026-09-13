"""Execution-boundary fingerprint including schema-v6 fit-to-short semantics."""

import copy
import hashlib
import json
import re

from base import compat_base as compat_impl
from guard import schema
from guard import schema_v4
from guard import schema_v5

CONTRACT_PROTOCOL_VERSION = compat_impl.CONTRACT_PROTOCOL_VERSION


def _canonical_bytes(payload):
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _legacy_v4_contract_hash():
    """Recreate the exact pre-v5 fingerprint for safe historical execution."""
    original_schema = compat_impl.schema
    try:
        compat_impl.schema = schema_v4
        return compat_impl.contract_hash()
    finally:
        compat_impl.schema = original_schema


def _legacy_v5_contract_hash():
    """Recreate the exact schema-v5 treatment fingerprint used before v6."""
    original_schema = compat_impl.schema
    try:
        compat_impl.schema = schema_v5
        payload = copy.deepcopy(compat_impl.contract_payload())
        payload["schema"].update(
            {
                "treatment_keys": sorted(schema_v5.TREATMENT_KEYS),
                "playback_rate_min": schema_v5.PLAYBACK_RATE_MIN,
                "playback_rate_max": schema_v5.PLAYBACK_RATE_MAX,
                "max_segment_start_seconds": schema_v5.MAX_SEGMENT_START_SECONDS,
                "min_segment_duration_seconds": schema_v5.MIN_SEGMENT_DURATION_SECONDS,
            }
        )
        return hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    finally:
        compat_impl.schema = original_schema


LEGACY_CONTRACT_HASHES = frozenset(
    {
        _legacy_v4_contract_hash(),
        _legacy_v5_contract_hash(),
        *compat_impl.LEGACY_CONTRACT_HASHES,
    }
)


def contract_payload():
    payload = copy.deepcopy(compat_impl.contract_payload())
    payload["schema"].update(
        {
            "treatment_keys": sorted(schema.TREATMENT_KEYS),
            "background_modes": [schema.FIT_TO_SHORT_MODE],
            "fit_playback_rate_min": schema.FIT_PLAYBACK_RATE_MIN,
            "fit_playback_rate_max": schema.FIT_PLAYBACK_RATE_MAX,
            "max_segment_start_seconds": schema.MAX_SEGMENT_START_SECONDS,
            "min_continuous_source_seconds": schema.MIN_CONTINUOUS_SOURCE_SECONDS,
            "preferred_continuous_range_seconds": schema.PREFERRED_CONTINUOUS_RANGE_SECONDS,
            "runtime_derived_playback_rate": True,
            "normal_loop_count": 0,
        }
    )
    return payload


def canonical_contract_bytes():
    return _canonical_bytes(contract_payload())


def contract_hash():
    return hashlib.sha256(canonical_contract_bytes()).hexdigest()


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def supported_contract_hashes():
    return frozenset({contract_hash(), *LEGACY_CONTRACT_HASHES})


def validate_contract_hash(value, *, allow_legacy_empty=False):
    normalized = str(value or "").strip().lower()
    if not normalized:
        if allow_legacy_empty:
            return ""
        raise ValueError("Missing compatibility fingerprint")
    if not _HASH_RE.fullmatch(normalized):
        raise ValueError("Invalid compatibility fingerprint")
    if normalized not in supported_contract_hashes():
        raise ValueError("Incompatible execution contract")
    return normalized


if __name__ == "__main__":
    print(contract_hash())
