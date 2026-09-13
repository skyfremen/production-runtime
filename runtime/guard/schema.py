"""Current public request schema with schema-v7 background sequences."""
import argparse
import copy
import math
from pathlib import Path

from base.contract import load_json
from guard import schema_v5 as legacy5

SCHEMA_VERSION = 7
SUPPORTED_SCHEMA_VERSIONS = {4, 5, 6, 7}
FORBIDDEN_KEYS = legacy5.FORBIDDEN_KEYS
TOP_LEVEL_KEYS = legacy5.TOP_LEVEL_KEYS
STORY_KEYS = legacy5.STORY_KEYS
NARRATION_KEYS = legacy5.NARRATION_KEYS
YOUTUBE_KEYS = legacy5.YOUTUBE_KEYS
PUBLICATION_KEYS = legacy5.PUBLICATION_KEYS
PLANNING_KEYS = legacy5.PLANNING_KEYS
ATTRIBUTE_KEYS = legacy5.ATTRIBUTE_KEYS
TITLE_CANDIDATE_KEYS = legacy5.TITLE_CANDIDATE_KEYS
LEAD_GENDERS = legacy5.LEAD_GENDERS
NATURAL_TONES = legacy5.NATURAL_TONES
EXPRESSIVE_TONES = legacy5.EXPRESSIVE_TONES
STORY_TONES = legacy5.STORY_TONES
APPROVED_VOICES = legacy5.APPROVED_VOICES
expected_voice = legacy5.expected_voice

FIT_TO_SHORT_MODE = "fit_to_short"
CONCATENATED_FIT_TO_SHORT_MODE = "concatenated_fit_to_short"
FIT_PLAYBACK_RATE_MIN = 1.0
FIT_PLAYBACK_RATE_MAX = 2.5
MIN_CONTINUOUS_SOURCE_SECONDS = 180.0
PREFERRED_CONTINUOUS_RANGE_SECONDS = 300.0
MIN_SEQUENCE_CLIP_SECONDS = 60.0
MIN_SEQUENCE_CLIPS = 2
MAX_SEQUENCE_CLIPS = 3
MIN_SEQUENCE_SOURCE_SECONDS = 210.0
PREFERRED_SEQUENCE_SOURCE_SECONDS = 240.0
MAX_SEQUENCE_SOURCE_SECONDS = 300.0
DURATION_EPSILON_SECONDS = 0.05
MAX_SEGMENT_START_SECONDS = legacy5.MAX_SEGMENT_START_SECONDS
LEGACY_VISUAL_KEYS = legacy5.LEGACY_VISUAL_KEYS
V6_TREATMENT_KEYS = frozenset({"mode", "segment_start_seconds", "segment_duration_seconds"})
V6_VISUAL_KEYS = frozenset({*LEGACY_VISUAL_KEYS, "background_primary_treatment", "background_backup_treatment"})
SEQUENCE_SEGMENT_KEYS = frozenset({"background_id", "segment_start_seconds", "segment_duration_seconds"})
V7_VISUAL_KEYS = frozenset({"background_mode", "background_primary_sequence", "background_backup_sequence"})
PLAYBACK_RATE_MIN = legacy5.PLAYBACK_RATE_MIN
PLAYBACK_RATE_MAX = legacy5.PLAYBACK_RATE_MAX
MIN_SEGMENT_DURATION_SECONDS = legacy5.MIN_SEGMENT_DURATION_SECONDS


def _number(value, label, errors, minimum=None, maximum=None):
    if isinstance(value, bool):
        errors.append(f"{label} must be numeric")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append(f"{label} must be numeric")
        return None
    if not math.isfinite(number):
        errors.append(f"{label} must be finite")
        return None
    if minimum is not None and number < minimum:
        errors.append(f"{label} must be >= {minimum:g}")
    if maximum is not None and number > maximum:
        errors.append(f"{label} must be <= {maximum:g}")
    return number


def validate_background_treatment(value, label="visual.background_treatment"):
    errors = []
    if not isinstance(value, dict) or set(value) != V6_TREATMENT_KEYS:
        return [f"{label} must contain exactly mode, segment_start_seconds, segment_duration_seconds"]
    if value.get("mode") != FIT_TO_SHORT_MODE:
        errors.append(f"{label}.mode must be {FIT_TO_SHORT_MODE}")
    _number(value.get("segment_start_seconds"), f"{label}.segment_start_seconds", errors, 0, MAX_SEGMENT_START_SECONDS)
    _number(value.get("segment_duration_seconds"), f"{label}.segment_duration_seconds", errors, MIN_CONTINUOUS_SOURCE_SECONDS)
    return errors


def validate_background_sequence(value, label="visual.background_sequence"):
    errors = []
    if not isinstance(value, list):
        return [f"{label} must be a list of {MIN_SEQUENCE_CLIPS}-{MAX_SEQUENCE_CLIPS} segments"]
    if not MIN_SEQUENCE_CLIPS <= len(value) <= MAX_SEQUENCE_CLIPS:
        errors.append(f"{label} must contain {MIN_SEQUENCE_CLIPS}-{MAX_SEQUENCE_CLIPS} segments")
    ids, total, numeric = [], 0.0, 0
    for index, segment in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(segment, dict) or set(segment) != SEQUENCE_SEGMENT_KEYS:
            errors.append(f"{item_label} must contain exactly background_id, segment_start_seconds, segment_duration_seconds")
            continue
        asset_id = segment.get("background_id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            errors.append(f"{item_label}.background_id must be a non-empty string")
        else:
            ids.append(asset_id)
        start = _number(segment.get("segment_start_seconds"), f"{item_label}.segment_start_seconds", errors, 0, MAX_SEGMENT_START_SECONDS)
        duration = _number(segment.get("segment_duration_seconds"), f"{item_label}.segment_duration_seconds", errors, MIN_SEQUENCE_CLIP_SECONDS)
        if start is not None and duration is not None:
            total += duration
            numeric += 1
    if len(ids) != len(set(ids)):
        errors.append(f"{label} must not repeat a background_id")
    if numeric == len(value) and value:
        if total < MIN_SEQUENCE_SOURCE_SECONDS - DURATION_EPSILON_SECONDS:
            errors.append(f"{label} total source duration must be >= {MIN_SEQUENCE_SOURCE_SECONDS:g}s")
        if total > MAX_SEQUENCE_SOURCE_SECONDS + DURATION_EPSILON_SECONDS:
            errors.append(f"{label} total source duration must be <= {MAX_SEQUENCE_SOURCE_SECONDS:g}s")
    return errors


def treatment_for_slot(request, slot):
    if slot not in {"primary", "backup"}:
        raise ValueError("background slot must be primary or backup")
    version = request.get("schema_version")
    if version in {4, 5}:
        return legacy5.treatment_for_slot(request, slot)
    if version != 6:
        raise ValueError("treatment_for_slot is only valid through schema v6")
    treatment = (request.get("visual") or {}).get(f"background_{slot}_treatment")
    errors = validate_background_treatment(treatment, f"visual.background_{slot}_treatment")
    if errors:
        raise ValueError("; ".join(errors))
    return {"mode": FIT_TO_SHORT_MODE, "segment_start_seconds": float(treatment["segment_start_seconds"]), "segment_duration_seconds": float(treatment["segment_duration_seconds"])}


def sequence_for_slot(request, slot):
    if slot not in {"primary", "backup"} or request.get("schema_version") != 7:
        raise ValueError("sequence_for_slot requires schema v7 and primary/backup slot")
    visual = request.get("visual") or {}
    if visual.get("background_mode") != CONCATENATED_FIT_TO_SHORT_MODE:
        raise ValueError("schema-v7 background_mode must be concatenated_fit_to_short")
    sequence = visual.get(f"background_{slot}_sequence")
    errors = validate_background_sequence(sequence, f"visual.background_{slot}_sequence")
    if errors:
        raise ValueError("; ".join(errors))
    return [{"background_id": str(x["background_id"]), "segment_start_seconds": float(x["segment_start_seconds"]), "segment_duration_seconds": float(x["segment_duration_seconds"])} for x in sequence]


def _as_v5_shape(data, primary_id=None, backup_id=None):
    candidate = copy.deepcopy(data)
    candidate["schema_version"] = 5
    visual = candidate.get("visual")
    if isinstance(visual, dict):
        primary_id = primary_id if primary_id is not None else visual.get("background_primary_id")
        backup_id = backup_id if backup_id is not None else visual.get("background_backup_id")
        candidate["visual"] = {
            "background_primary_id": primary_id,
            "background_backup_id": backup_id,
            "background_primary_treatment": {"segment_start_seconds": 0.0, "segment_duration_seconds": None, "playback_rate": 1.0},
            "background_backup_treatment": {"segment_start_seconds": 0.0, "segment_duration_seconds": None, "playback_rate": 1.0},
        }
    return candidate


def _validate_v6(data, request_path=None):
    errors = []
    visual = data.get("visual")
    if not isinstance(visual, dict):
        errors.append("visual must be an object")
    else:
        missing, extra = V6_VISUAL_KEYS - set(visual), set(visual) - V6_VISUAL_KEYS
        if missing:
            errors.append("visual missing fields: " + ", ".join(sorted(missing)))
        if extra:
            errors.append("visual unexpected fields: " + ", ".join(sorted(extra)))
        for slot in ("primary", "backup"):
            errors.extend(validate_background_treatment(visual.get(f"background_{slot}_treatment"), f"visual.background_{slot}_treatment"))
    return legacy5.validate_request_data(_as_v5_shape(data), request_path=request_path) + errors


def _validate_v7(data, request_path=None):
    errors = []
    visual = data.get("visual")
    primary = backup = None
    if not isinstance(visual, dict):
        errors.append("visual must be an object")
    else:
        missing, extra = V7_VISUAL_KEYS - set(visual), set(visual) - V7_VISUAL_KEYS
        if missing:
            errors.append("visual missing fields: " + ", ".join(sorted(missing)))
        if extra:
            errors.append("visual unexpected fields: " + ", ".join(sorted(extra)))
        if visual.get("background_mode") != CONCATENATED_FIT_TO_SHORT_MODE:
            errors.append(f"visual.background_mode must be {CONCATENATED_FIT_TO_SHORT_MODE}")
        primary = visual.get("background_primary_sequence")
        backup = visual.get("background_backup_sequence")
        errors.extend(validate_background_sequence(primary, "visual.background_primary_sequence"))
        errors.extend(validate_background_sequence(backup, "visual.background_backup_sequence"))
        if isinstance(primary, list) and isinstance(backup, list):
            pids = {x.get("background_id") for x in primary if isinstance(x, dict)}
            bids = {x.get("background_id") for x in backup if isinstance(x, dict)}
            overlap = sorted(x for x in pids & bids if isinstance(x, str))
            if overlap:
                errors.append("primary and backup background sequences must be disjoint; overlap=" + ", ".join(overlap))
    first_primary = primary[0].get("background_id") if isinstance(primary, list) and primary and isinstance(primary[0], dict) else "__invalid_primary__"
    first_backup = backup[0].get("background_id") if isinstance(backup, list) and backup and isinstance(backup[0], dict) else "__invalid_backup__"
    return legacy5.validate_request_data(_as_v5_shape(data, first_primary, first_backup), request_path=request_path) + errors


def validate_request_data(data, request_path=None):
    if not isinstance(data, dict):
        return ["request root must be an object"]
    version = data.get("schema_version")
    if version in {4, 5}:
        return legacy5.validate_request_data(data, request_path=request_path)
    if version == 6:
        return _validate_v6(data, request_path)
    if version == 7:
        return _validate_v7(data, request_path)
    return ["schema_version must be 4, 5, 6 or 7"]


def validate_request(path):
    path = Path(path)
    data = load_json(path)
    errors = validate_request_data(data, request_path=path)
    if errors:
        raise SystemExit("Request validation failed:\n- " + "\n- ".join(errors))
    print(f"Request valid: {path.name}; schema={data['schema_version']}")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    validate_request(args.request)


if __name__ == "__main__":
    main()
