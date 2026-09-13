"""Caption/render entry point with schema-v6 continuous-background preparation."""
import os
import sys

from base.contract import atomic_write_json, load_json
from resources.resolve import apply_fit_to_short_treatment
from transform import process_base as base
from transform.process_base import *

# ``import *`` intentionally omits underscore-prefixed compatibility helpers.
# Keep the established public test/module surface stable while the v6 wrapper
# intercepts only the final background composition call.
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
    raise RuntimeError(
        "schema-v6 compositor command is missing expected background loop marker"
    )


def _final_duration_from_command(command):
    try:
        index = len(command) - 1 - list(reversed(command)).index("-t")
        return float(command[index + 1])
    except (ValueError, IndexError, TypeError):
        raise RuntimeError(
            "cannot derive final render duration from compositor command"
        ) from None


def continuous_render_capture(command):
    if not _CURRENT_REQUEST_PATH or "-stream_loop" not in command:
        return _BASE_CAPTURE(command)
    request = load_json(_CURRENT_REQUEST_PATH)
    if request.get("schema_version") != 6:
        return _BASE_CAPTURE(command)

    final_duration = _final_duration_from_command(command)
    selection_path = base.render.OUTPUT_DIR / "background_selection.json"
    background_path = base.render.OUTPUT_DIR / "background.asset"
    selection = load_json(selection_path)
    if selection.get("background_treatment_pending") is not True:
        raise RuntimeError("schema-v6 background treatment was not deferred correctly")

    caption_score = selection.get("background_caption_readability_score")
    if caption_score is None:
        readability = selection.get("readability") or {}
        caption_score = readability.get("registry_score")
    if caption_score is None:
        raise RuntimeError(
            "schema-v6 deferred treatment is missing registered caption readability score"
        )

    metrics = apply_fit_to_short_treatment(
        background_path,
        selection.get("background_treatment"),
        final_duration,
        caption_score,
        test_mode=os.getenv("STORY_TEST_MODE", "").strip().lower()
        in {"1", "true", "yes", "on"},
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

    # Preserve the established caption/alignment module while replacing only its
    # bounded final FFmpeg capture for schema-v6 requests.
    base.bounded_render_capture = continuous_render_capture
    base.main()


if __name__ == "__main__":
    main()
