"""Current public request schema.

Schema v4/v5 remain executable recovery formats. Schema v6 freezes a long
continuous source range while runtime derives exact playback speed from actual
post-TTS render duration.
"""
import argparse
import copy
import math
from pathlib import Path

from base.contract import load_json
from guard import schema_v5 as legacy5

SCHEMA_VERSION = 6
SUPPORTED_SCHEMA_VERSIONS = {4, 5, 6}
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
FIT_PLAYBACK_RATE_MIN = 1.0
FIT_PLAYBACK_RATE_MAX = 2.5
MIN_CONTINUOUS_SOURCE_SECONDS = 180.0
PREFERRED_CONTINUOUS_RANGE_SECONDS = 300.0
MAX_SEGMENT_START_SECONDS = legacy5.MAX_SEGMENT_START_SECONDS
LEGACY_VISUAL_KEYS = legacy5.LEGACY_VISUAL_KEYS
TREATMENT_KEYS = frozenset(
    {"mode", "segment_start_seconds", "segment_duration_seconds"}
)
VISUAL_KEYS = frozenset(
    {
        *LEGACY_VISUAL_KEYS,
        "background_primary_treatment",
        "background_backup_treatment",
    }
)

# Preserve the old public constant surface for historical callers. These aliases
# describe v5 fixed-rate treatments only; all new v6 code must use FIT_* values.
PLAYBACK_RATE_MIN = legacy5.PLAYBACK_RATE_MIN
PLAYBACK_RATE_MAX = legacy5.PLAYBACK_RATE_MAX
MIN_SEGMENT_DURATION_SECONDS = legacy5.MIN_SEGMENT_DURATION_SECONDS


def _number(value, label, errors, *, minimum=None, maximum=None):
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
    if not isinstance(value, dict) or set(value) != TREATMENT_KEYS:
        return [
            f"{label} must contain exactly mode, segment_start_seconds, "
            "segment_duration_seconds"
        ]
    if value.get("mode") != FIT_TO_SHORT_MODE:
        errors.append(f"{label}.mode must be {FIT_TO_SHORT_MODE}")
    _number(
        value.get("segment_start_seconds"),
        f"{label}.segment_start_seconds",
        errors,
        minimum=0.0,
        maximum=MAX_SEGMENT_START_SECONDS,
    )
    _number(
        value.get("segment_duration_seconds"),
        f"{label}.segment_duration_seconds",
        errors,
        minimum=MIN_CONTINUOUS_SOURCE_SECONDS,
    )
    return errors


def treatment_for_slot(request, slot):
    if slot not in {"primary", "backup"}:
        raise ValueError("background slot must be primary or backup")
    version = request.get("schema_version")
    if version in {4, 5}:
        return legacy5.treatment_for_slot(request, slot)
    if version != 6:
        raise ValueError("unsupported request schema")
    treatment = (request.get("visual") or {}).get(f"background_{slot}_treatment")
    errors = validate_background_treatment(
        treatment, f"visual.background_{slot}_treatment"
    )
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "mode": FIT_TO_SHORT_MODE,
        "segment_start_seconds": float(treatment["segment_start_seconds"]),
        "segment_duration_seconds": float(treatment["segment_duration_seconds"]),
    }


def _as_v5_shape(data):
    candidate = copy.deepcopy(data)
    candidate["schema_version"] = 5
    visual = candidate.get("visual")
    if isinstance(visual, dict):
        for slot in ("primary", "backup"):
            visual[f"background_{slot}_treatment"] = {
                "segment_start_seconds": 0.0,
                "segment_duration_seconds": None,
                "playback_rate": 1.0,
            }
    return candidate


def validate_request_data(data, request_path=None):
    if not isinstance(data, dict):
        return ["request root must be an object"]
    version = data.get("schema_version")
    if version in {4, 5}:
        return legacy5.validate_request_data(data, request_path=request_path)
    if version != 6:
        return ["schema_version must be 4, 5 or 6"]

    errors = []
    visual = data.get("visual")
    if not isinstance(visual, dict):
        errors.append("visual must be an object")
    else:
        missing = VISUAL_KEYS - set(visual)
        extra = set(visual) - VISUAL_KEYS
        if missing:
            errors.append("visual missing fields: " + ", ".join(sorted(missing)))
        if extra:
            errors.append("visual unexpected fields: " + ", ".join(sorted(extra)))
        for slot in ("primary", "backup"):
            errors.extend(
                validate_background_treatment(
                    visual.get(f"background_{slot}_treatment"),
                    f"visual.background_{slot}_treatment",
                )
            )
    return (
        legacy5.validate_request_data(
            _as_v5_shape(data), request_path=request_path
        )
        + errors
    )


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
