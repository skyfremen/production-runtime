"""Deterministic compatibility fingerprint for the execution boundary."""

import hashlib
import json
import re

from base import contract as base
from guard import schema
from profile import config as profile
from resources import policy as media_policy

CONTRACT_PROTOCOL_VERSION = 1
_SAMPLE_CONTENT_ID = "wd-20990101T000000-sample-test-a1b2c3"


def _sorted(values):
    return sorted(values)


def _weights(values):
    return {key: values[key] for key in sorted(values)}


def _crop_vector(width, height):
    geometry = media_policy.crop_fill_geometry(width, height)
    return {
        key: round(float(geometry[key]), 8)
        for key in (
            "scale_factor",
            "scaled_width",
            "scaled_height",
            "source_crop_width",
            "source_crop_height",
        )
    }


def _rendition_vector(rendition):
    return {
        "suitable": media_policy.rendition_is_production_suitable(rendition),
        "sort_key": list(media_policy.rendition_sort_key(rendition)),
    }


def contract_payload():
    output = {
        "width": base.DEFAULT_VIDEO_WIDTH,
        "height": base.DEFAULT_VIDEO_HEIGHT,
        "fps": base.DEFAULT_VIDEO_FPS,
    }
    voice_cases = (
        ("female", "natural"),
        ("female", "dramatic"),
        ("male", "natural"),
        ("male", "dramatic"),
    )
    renditions = {
        "exact_vertical": {
            "id": "v1",
            "file_type": "video/mp4",
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "file_size_bytes": 1_000_000,
        },
        "hd_landscape": {
            "id": "v2",
            "file_type": "video/mp4",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "file_size_bytes": 1_000_000,
        },
        "uhd_landscape": {
            "id": "v3",
            "file_type": "video/mp4",
            "width": 3840,
            "height": 2160,
            "fps": 30,
            "file_size_bytes": 2_000_000,
        },
    }
    return {
        "protocol": CONTRACT_PROTOCOL_VERSION,
        "base": {
            "content_id_pattern": base.CONTENT_ID_RE.pattern,
            "production_max_seconds": base.PRODUCTION_MAX_SECONDS,
            "production_target_min_seconds": base.PRODUCTION_TARGET_MIN_SECONDS,
            "production_target_max_seconds": base.PRODUCTION_TARGET_MAX_SECONDS,
            "start_lead_seconds": base.START_LEAD_SECONDS,
            "end_tail_seconds": base.END_TAIL_SECONDS,
            "encode_safety_seconds": base.PRODUCTION_ENCODE_SAFETY_SECONDS,
            "tag_max_chars": base.YOUTUBE_TAG_MAX_CHARS,
            "output": output,
        },
        "media": {
            "target_width": media_policy.TARGET_WIDTH,
            "target_height": media_policy.TARGET_HEIGHT,
            "target_fps": media_policy.TARGET_FPS,
            "supported_types": _sorted(media_policy.SUPPORTED_TYPES),
            "max_source_pixels": media_policy.MAX_SOURCE_PIXELS,
            "max_crop_fill_upscale": media_policy.MAX_CROP_FILL_UPSCALE,
        },
        "profile": {
            "timezone": profile.CANONICAL_TIMEZONE,
            "editorial_weights": _weights(profile.EDITORIAL_WEIGHTS),
            "title_weights": _weights(profile.TITLE_WEIGHTS),
            "categories": _sorted(profile.CATEGORIES),
            "emotions": _sorted(profile.EMOTIONS),
            "opening_styles": _sorted(profile.OPENING_STYLES),
            "title_styles": _sorted(profile.TITLE_STYLES),
            "ending_styles": _sorted(profile.ENDING_STYLES),
            "protagonist_roles": _sorted(profile.PROTAGONIST_ROLES),
            "antagonist_roles": _sorted(profile.ANTAGONIST_ROLES),
        },
        "schema": {
            "current_version": schema.SCHEMA_VERSION,
            "supported_versions": _sorted(schema.SUPPORTED_SCHEMA_VERSIONS),
            "forbidden_keys": _sorted(schema.FORBIDDEN_KEYS),
            "top_level_keys": _sorted(schema.TOP_LEVEL_KEYS),
            "story_keys": _sorted(schema.STORY_KEYS),
            "story_v4_keys": _sorted(schema.STORY_V4_KEYS),
            "narration_keys": _sorted(schema.NARRATION_KEYS),
            "visual_keys": _sorted(schema.VISUAL_KEYS),
            "youtube_keys": _sorted(schema.YOUTUBE_KEYS),
            "publication_keys": _sorted(schema.PUBLICATION_KEYS),
            "planning_keys": _sorted(schema.PLANNING_KEYS),
            "attribute_keys": _sorted(schema.ATTRIBUTE_KEYS),
            "title_candidate_keys": _sorted(schema.TITLE_CANDIDATE_KEYS),
            "lead_genders": _sorted(schema.LEAD_GENDERS),
            "natural_tones": _sorted(schema.NATURAL_TONES),
            "expressive_tones": _sorted(schema.EXPRESSIVE_TONES),
            "approved_voices": _sorted(schema.APPROVED_VOICES),
            "narration_speed": 1.75,
        },
        "behavior": {
            "marker": base.marker_tag(_SAMPLE_CONTENT_ID),
            "voices": {
                f"{gender}:{tone}": schema.expected_voice(gender, tone)
                for gender, tone in voice_cases
            },
            "crop": {
                "exact_vertical": _crop_vector(1080, 1920),
                "hd_landscape": _crop_vector(1920, 1080),
                "uhd_landscape": _crop_vector(3840, 2160),
            },
            "renditions": {
                name: _rendition_vector(value)
                for name, value in sorted(renditions.items())
            },
        },
    }


def canonical_contract_bytes():
    return json.dumps(
        contract_payload(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def contract_hash():
    return hashlib.sha256(canonical_contract_bytes()).hexdigest()


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
# During a future contract migration, add the old production fingerprint here
# before deploying the new semantics. Remove it after the private producer
# has switched to the new fingerprint.
LEGACY_CONTRACT_HASHES = frozenset()


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
