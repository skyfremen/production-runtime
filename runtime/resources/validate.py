"""Active runtime background-registry validation.

The active registry may be empty between hard reset and automatic replenishment.
Soft-retired entries are not allowed in the active registry.
"""
import json
from pathlib import Path

from guard.schema import FIT_TO_SHORT_MODE, MIN_CONTINUOUS_SOURCE_SECONDS
from resources import validate_base as base
from resources.validate_base import *

REGISTRY_PATH = base.REGISTRY_PATH
_DURATION_EPSILON = 0.05


def validate_registry_data(data):
    if not isinstance(data, dict): return ["background registry root must be an object"]
    if data.get("schema_version") != 3: return ["schema_version must be 3"]
    assets = data.get("assets")
    if not isinstance(assets, list): return ["assets must be a list"]
    if not assets: return []
    errors = base.validate_registry_data(data)
    for index, asset in enumerate(assets, 1):
        if isinstance(asset, dict) and "selection_enabled" in asset:
            errors.append(f"asset {index}: selection_enabled is obsolete in the active registry")
    return errors


def load_registry(path=REGISTRY_PATH):
    data = json.loads(Path(path).read_text(encoding="utf-8")); errors = validate_registry_data(data)
    if errors: raise ValueError("Background registry validation failed:\n- " + "\n- ".join(errors))
    return data


def asset_map(data): return {asset["id"]: asset for asset in data.get("assets", [])}


def validate_request_backgrounds(request_data, registry_data):
    mapping = asset_map(registry_data); visual = request_data["visual"]
    primary, backup = visual["background_primary_id"], visual["background_backup_id"]
    if primary == backup: raise ValueError("Primary and backup background IDs must differ")
    missing = [value for value in (primary, backup) if value not in mapping]
    if missing: raise ValueError("Unknown background IDs: " + ", ".join(missing))
    for slot, asset_id in (("primary", primary), ("backup", backup)):
        asset = mapping[asset_id]
        if asset.get("status") != "active" or asset.get("verified") is not True: raise ValueError(f"Background {asset_id} must be active and verified")
        if request_data.get("schema_version") == 6:
            treatment = visual.get(f"background_{slot}_treatment") or {}
            if treatment.get("mode") != FIT_TO_SHORT_MODE: raise ValueError(f"Background {asset_id} must use fit_to_short")
            try:
                source_duration = float(asset.get("duration_seconds")); start = float(treatment["segment_start_seconds"]); duration = float(treatment["segment_duration_seconds"])
            except (KeyError, TypeError, ValueError): raise ValueError(f"Background {asset_id} requires trusted duration and continuous range") from None
            if source_duration < MIN_CONTINUOUS_SOURCE_SECONDS: raise ValueError(f"Background {asset_id} is too short for continuous fit-to-short")
            if duration < MIN_CONTINUOUS_SOURCE_SECONDS: raise ValueError(f"Background {asset_id} selected range is too short for continuous fit-to-short")
            if start < 0 or start + duration > source_duration + _DURATION_EPSILON: raise ValueError(f"Background {asset_id} selected range exceeds source duration")
    return mapping[primary], mapping[backup]


def main():
    try: data = load_registry()
    except ValueError as exc: raise SystemExit(str(exc))
    active = sum(1 for item in data["assets"] if item.get("status") == "active")
    print(f"Active background registry valid: {len(data['assets'])} assets, {active} active.")


if __name__ == "__main__": main()
