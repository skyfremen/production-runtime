"""Versioned request schema with backward-compatible background treatments.

Schema v4 remains valid during migration. Schema v5 adds one immutable treatment
for each requested logical background so the private planner can reserve temporal
segments and playback rates while the public runtime only executes them.
"""

import argparse
import copy
from pathlib import Path

from base.contract import load_json
from guard import schema_v4 as legacy

SCHEMA_VERSION = 5
SUPPORTED_SCHEMA_VERSIONS = {4, 5}
SUPPORTED_SCHEMA_TEXT = " or ".join(str(value) for value in sorted(SUPPORTED_SCHEMA_VERSIONS))
FORBIDDEN_KEYS = legacy.FORBIDDEN_KEYS
TOP_LEVEL_KEYS = legacy.TOP_LEVEL_KEYS
STORY_KEYS = legacy.STORY_KEYS
NARRATION_KEYS = legacy.NARRATION_KEYS
YOUTUBE_KEYS = legacy.YOUTUBE_KEYS
PUBLICATION_KEYS = legacy.PUBLICATION_KEYS
PLANNING_KEYS = legacy.PLANNING_KEYS
ATTRIBUTE_KEYS = legacy.ATTRIBUTE_KEYS
TITLE_CANDIDATE_KEYS = legacy.TITLE_CANDIDATE_KEYS
LEAD_GENDERS = legacy.LEAD_GENDERS
NATURAL_TONES = legacy.NATURAL_TONES
EXPRESSIVE_TONES = legacy.EXPRESSIVE_TONES
STORY_TONES = legacy.STORY_TONES
APPROVED_VOICES = legacy.APPROVED_VOICES
expected_voice = legacy.expected_voice

LEGACY_VISUAL_KEYS = frozenset({"background_primary_id", "background_backup_id"})
TREATMENT_KEYS = frozenset({
    "segment_start_seconds",
    "segment_duration_seconds",
    "playback_rate",
})
VISUAL_KEYS = frozenset({
    *LEGACY_VISUAL_KEYS,
    "background_primary_treatment",
    "background_backup_treatment",
})
PLAYBACK_RATE_MIN = 1.0
PLAYBACK_RATE_MAX = 2.0
MAX_SEGMENT_START_SECONDS = 24 * 60 * 60
MIN_SEGMENT_DURATION_SECONDS = 1.0


def _number(value, label, errors, *, minimum=None, maximum=None, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool):
        errors.append(f"{label} must be numeric" + (" or null" if nullable else ""))
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append(f"{label} must be numeric" + (" or null" if nullable else ""))
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
            f"{label} must contain exactly segment_start_seconds, "
            "segment_duration_seconds, playback_rate"
        ]
    start = _number(
        value.get("segment_start_seconds"),
        f"{label}.segment_start_seconds",
        errors,
        minimum=0.0,
        maximum=MAX_SEGMENT_START_SECONDS,
    )
    duration = _number(
        value.get("segment_duration_seconds"),
        f"{label}.segment_duration_seconds",
        errors,
        minimum=MIN_SEGMENT_DURATION_SECONDS,
        nullable=True,
    )
    _number(
        value.get("playback_rate"),
        f"{label}.playback_rate",
        errors,
        minimum=PLAYBACK_RATE_MIN,
        maximum=PLAYBACK_RATE_MAX,
    )
    if duration is None and start not in (None, 0.0):
        errors.append(
            f"{label}.segment_start_seconds must be 0 when segment_duration_seconds is null"
        )
    return errors


def treatment_for_slot(request, slot):
    """Return the frozen v5 treatment, or the v4 identity treatment."""
    if slot not in {"primary", "backup"}:
        raise ValueError("background slot must be primary or backup")
    if request.get("schema_version") == 4:
        return {
            "segment_start_seconds": 0.0,
            "segment_duration_seconds": None,
            "playback_rate": 1.0,
        }
    visual = request.get("visual") or {}
    treatment = visual.get(f"background_{slot}_treatment")
    errors = validate_background_treatment(
        treatment, f"visual.background_{slot}_treatment"
    )
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "segment_start_seconds": float(treatment["segment_start_seconds"]),
        "segment_duration_seconds": (
            None
            if treatment["segment_duration_seconds"] is None
            else float(treatment["segment_duration_seconds"])
        ),
        "playback_rate": float(treatment["playback_rate"]),
    }


def _legacy_view(data):
    candidate = copy.deepcopy(data)
    candidate["schema_version"] = 4
    visual = candidate.get("visual")
    if isinstance(visual, dict):
        candidate["visual"] = {
            key: visual.get(key) for key in LEGACY_VISUAL_KEYS
        }
    return candidate


def validate_request_data(data, request_path=None):
    if not isinstance(data, dict):
        return ["request root must be an object"]
    version = data.get("schema_version")
    if version == 4:
        return legacy.validate_request_data(data, request_path=request_path)
    if version != 5:
        return [f"schema_version must be {SUPPORTED_SCHEMA_TEXT}"]

    visual = data.get("visual")
    treatment_errors = []
    if not isinstance(visual, dict):
        treatment_errors.append("visual must be an object")
    else:
        missing = VISUAL_KEYS - set(visual)
        extra = set(visual) - VISUAL_KEYS
        if missing:
            treatment_errors.append(
                "visual missing fields: " + ", ".join(sorted(missing))
            )
        if extra:
            treatment_errors.append(
                "visual unexpected fields: " + ", ".join(sorted(extra))
            )
        for slot in ("primary", "backup"):
            treatment_errors.extend(
                validate_background_treatment(
                    visual.get(f"background_{slot}_treatment"),
                    f"visual.background_{slot}_treatment",
                )
            )

    errors = legacy.validate_request_data(
        _legacy_view(data), request_path=request_path
    )
    return errors + treatment_errors


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
