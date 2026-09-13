"""Schema-v6 dry-run facade over the established production-equivalence harness.

The established visual assertions live in exercise_base. This facade upgrades its
synthetic fixtures to schema v6 so Dry Run also proves continuous fit-to-short
execution with zero normal background loops.
"""
import json
from pathlib import Path

import exercise_base as base
from engine import check
from transform import compose as render

_legacy_synthetic_request = check.synthetic_request
_legacy_write_registry = base.write_synthetic_registry
_legacy_render_smoke = check.render_smoke
_legacy_full_preview = base._render_full_visual_preview


def synthetic_request_v6(index):
    request = _legacy_synthetic_request(index)
    request["schema_version"] = 6
    request["visual"] = {
        "background_primary_id": "satisfying-001",
        "background_backup_id": "satisfying-002",
        "background_primary_treatment": {
            "mode": "fit_to_short",
            "segment_start_seconds": 0.0,
            "segment_duration_seconds": 300.0,
        },
        "background_backup_treatment": {
            "mode": "fit_to_short",
            "segment_start_seconds": 0.0,
            "segment_duration_seconds": 300.0,
        },
    }
    return request


def write_synthetic_registry_v6():
    _legacy_write_registry()
    target = Path(__file__).resolve().parent / "media-library/backgrounds.json"
    registry = json.loads(target.read_text(encoding="utf-8"))
    for asset in registry.get("assets", []):
        asset["duration_seconds"] = 300.0
        asset.pop("selection_enabled", None)
    target.write_text(json.dumps(registry) + "\n", encoding="utf-8")


def make_background_v6(path):
    render.run_capture(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=40.0",
            "-an", "-c:v", "libx264", "-preset", render.X264_PRESET,
            "-crf", str(render.X264_CRF), "-pix_fmt", "yuv420p",
            "-f", "mp4", str(path),
        ]
    )


def render_smoke_v6(request_path, root):
    result = _legacy_render_smoke(request_path, root)
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    target = Path(root) / "render-smoke" / "background_selection.json"
    selection = json.loads(target.read_text(encoding="utf-8"))
    selection.update({
        "background_selection": "primary",
        "background_asset_id": request["visual"]["background_primary_id"],
        "background_treatment": request["visual"]["background_primary_treatment"],
        "background_treatment_pending": True,
        "background_caption_readability_score": 90.0,
    })
    target.write_text(json.dumps(selection, indent=2) + "\n", encoding="utf-8")
    return result


def full_visual_preview_v6(root, request_path):
    output, metadata, metrics = _legacy_full_preview(root, request_path)
    selection = json.loads(
        (Path(root) / "visual-preview" / "background_selection.json").read_text(
            encoding="utf-8"
        )
    )
    treatment_metrics = selection.get("metrics") or {}
    if treatment_metrics.get("background_treatment_mode") != "fit_to_short":
        raise RuntimeError("Dry Run did not execute fit_to_short treatment")
    if treatment_metrics.get("background_treatment_loop_mode") != "none":
        raise RuntimeError("Dry Run unexpectedly looped continuous background")
    if int(treatment_metrics.get("background_treatment_loop_count", -1)) != 0:
        raise RuntimeError("Dry Run must record zero continuous-background loops")
    if selection.get("background_treatment_pending") is not False:
        raise RuntimeError("Dry Run left continuous treatment pending")
    metrics["background_fit_mode"] = "fit_to_short"
    metrics["background_loop_count"] = 0
    metrics["background_derived_playback_rate"] = treatment_metrics.get(
        "background_treatment_derived_playback_rate"
    )
    return output, metadata, metrics


def main():
    check.synthetic_request = synthetic_request_v6
    base.write_synthetic_registry = write_synthetic_registry_v6
    check._make_background = make_background_v6
    check.render_smoke = render_smoke_v6
    base._render_full_visual_preview = full_visual_preview_v6
    return base.main()


if __name__ == "__main__":
    main()
