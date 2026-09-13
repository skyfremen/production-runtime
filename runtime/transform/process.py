"""Caption/render entry point with schema-v6/v7 no-loop background preparation."""
import os
import sys

from base.contract import atomic_write_json, load_json
from resources.resolve import (
    apply_concatenated_fit_to_short_treatment,
    apply_fit_to_short_treatment,
)
from resources.validate import load_registry
from transform import process_base as base
from transform.process_base import *

_caption_ass_focus_text = base._caption_ass_focus_text
_env_flag = base._env_flag
_word_highlight_enabled = base._word_highlight_enabled
_semantic_emphasis_enabled = base._semantic_emphasis_enabled
_dialogue = base._dialogue
_rapid_units = base._rapid_units
_semantic_local_punchline_indices = base._semantic_local_punchline_indices
_semantic_split_before = base._semantic_split_before
_semantic_metadata = base._semantic_metadata
_request_punchline_from_argv = base._request_punchline_from_argv

_BASE_CAPTURE = base.bounded_render_capture
_CURRENT_REQUEST_PATH = None


def _strip_background_stream_loop(command):
    command = list(command)
    for index in range(len(command) - 1):
        if command[index] == "-stream_loop" and command[index + 1] == "-1":
            return command[:index] + command[index + 2:]
    raise RuntimeError("no-loop compositor command is missing expected background loop marker")


def _final_duration_from_command(command):
    try:
        index = len(command) - 1 - list(reversed(command)).index("-t")
        return float(command[index + 1])
    except (ValueError, IndexError, TypeError):
        raise RuntimeError("cannot derive final render duration from compositor command") from None


def _sequence_caption_readability_score(selection, registry):
    mapping = {
        str(item.get("id")): item
        for item in registry.get("assets", [])
        if isinstance(item, dict)
    }
    sequence = selection.get("background_sequence")
    if not isinstance(sequence, list) or not sequence:
        raise RuntimeError("schema-v7 selected background sequence is missing")
    scores = []
    for segment in sequence:
        if not isinstance(segment, dict):
            raise RuntimeError("schema-v7 selected background sequence is invalid")
        asset_id = str(segment.get("background_id") or "")
        asset = mapping.get(asset_id)
        if not isinstance(asset, dict):
            raise RuntimeError(f"schema-v7 selected background asset is missing: {asset_id}")
        try:
            score = float(asset["caption_readability_score"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError(
                f"schema-v7 selected background is missing caption readability: {asset_id}"
            ) from None
        scores.append(score)
    return min(scores)


def continuous_render_capture(command):
    if not _CURRENT_REQUEST_PATH or "-stream_loop" not in command:
        return _BASE_CAPTURE(command)
    request = load_json(_CURRENT_REQUEST_PATH)
    version = request.get("schema_version")
    if version not in {6, 7}:
        return _BASE_CAPTURE(command)

    final_duration = _final_duration_from_command(command)
    selection_path = base.render.OUTPUT_DIR / "background_selection.json"
    background_path = base.render.OUTPUT_DIR / "background.asset"
    selection = load_json(selection_path)
    if selection.get("background_treatment_pending") is not True:
        raise RuntimeError(f"schema-v{version} background treatment was not deferred correctly")

    test_mode = os.getenv("STORY_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
    if version == 6:
        caption_score = selection.get("background_caption_readability_score")
        if caption_score is None:
            readability = selection.get("readability") or {}
            caption_score = readability.get("registry_score")
        if caption_score is None:
            raise RuntimeError("schema-v6 deferred treatment is missing caption readability score")
        metrics = apply_fit_to_short_treatment(
            background_path,
            selection.get("background_treatment"),
            final_duration,
            caption_score,
            test_mode=test_mode,
        )
    else:
        caption_score = _sequence_caption_readability_score(selection, load_registry())
        metrics = apply_concatenated_fit_to_short_treatment(
            background_path,
            final_duration,
            caption_score,
            test_mode=test_mode,
        )

    selection["background_treatment_pending"] = False
    selection["readability"] = metrics.pop("readability")
    selection.setdefault("metrics", {}).update(metrics)
    atomic_write_json(selection_path, selection)
    return _BASE_CAPTURE(_strip_background_stream_loop(command))


def main():
    global _CURRENT_REQUEST_PATH
    try:
        position = sys.argv.index("--request")
        _CURRENT_REQUEST_PATH = sys.argv[position + 1]
    except (ValueError, IndexError):
        _CURRENT_REQUEST_PATH = None
    base.bounded_render_capture = continuous_render_capture
    base.main()


if __name__ == "__main__":
    main()
