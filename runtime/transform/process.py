"""V1 caption/render entry point with deferred no-loop background treatment."""
import os
import sys

from base.contract import atomic_write_json, load_json
from resources.resolve import apply_concatenated_fit_to_short_treatment
from resources.validate import asset_map, load_registry
from transform import process_base as base
from transform.process_base import *  # preserve proven alignment/caption surface

_BASE_CAPTURE = base.bounded_render_capture
_CURRENT_REQUEST_PATH = None


def _strip_background_stream_loop(command):
    command = list(command)
    for index in range(len(command) - 1):
        if command[index] == "-stream_loop" and command[index + 1] == "-1":
            return command[:index] + command[index + 2 :]
    raise RuntimeError(
        "no-loop V1 compositor command is missing expected background loop marker"
    )


def _final_duration_from_command(command):
    try:
        index = len(command) - 1 - list(reversed(command)).index("-t")
        return float(command[index + 1])
    except (ValueError, IndexError, TypeError):
        raise RuntimeError("cannot derive final render duration") from None


def _caption_readability_score(request):
    registry = load_registry()
    mapping = asset_map(registry)
    scores = []
    for segment in request["background"]["segments"]:
        asset_id = segment["background_id"]
        asset = mapping.get(asset_id)
        if not isinstance(asset, dict):
            raise RuntimeError(f"selected background is missing: {asset_id}")
        try:
            scores.append(float(asset["caption_readability_score"]))
        except (KeyError, TypeError, ValueError):
            raise RuntimeError(
                f"selected background lacks caption readability score: {asset_id}"
            ) from None
    return min(scores)


def v1_render_capture(command):
    if not _CURRENT_REQUEST_PATH or "-stream_loop" not in command:
        return _BASE_CAPTURE(command)
    request = load_json(_CURRENT_REQUEST_PATH)
    if request.get("request_version") != 1:
        raise RuntimeError("Only Wacky Dramas request_version 1 is supported")
    selection_path = base.render.OUTPUT_DIR / "background_selection.json"
    background_path = base.render.OUTPUT_DIR / "background.asset"
    selection = load_json(selection_path)
    if selection.get("background_treatment_pending") is not True:
        raise RuntimeError("V1 background treatment was not deferred correctly")
    final_duration = _final_duration_from_command(command)
    test_mode = os.getenv("STORY_TEST_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    metrics = apply_concatenated_fit_to_short_treatment(
        background_path,
        final_duration,
        _caption_readability_score(request),
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
    base.bounded_render_capture = v1_render_capture
    base.main()


if __name__ == "__main__":
    main()
