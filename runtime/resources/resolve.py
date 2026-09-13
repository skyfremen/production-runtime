"""Schema-v6 continuous background resolver with v4/v5 compatibility.

New schema-v6 requests defer temporal treatment until the renderer knows the exact
post-TTS output duration. Historical v4/v5 requests continue through the retained
v5 resolver. This facade preserves the established patchable module surface used
by tests and callers while keeping the new continuous-fit path isolated.
"""
import argparse
import subprocess
import time
from pathlib import Path

from base.contract import atomic_write_json, load_json
from guard.schema import (
    FIT_PLAYBACK_RATE_MAX,
    FIT_PLAYBACK_RATE_MIN,
    FIT_TO_SHORT_MODE,
    treatment_for_slot,
)
from resources import resolve_v5 as legacy5
from resources.resolve_v5 import *

# Capture the retained v5 callables before defining facade wrappers.
_LEGACY_PREFLIGHT = legacy5.preflight
_LEGACY_NORMALIZE_FOR_RENDER = legacy5.normalize_for_render
_LEGACY_DOWNLOAD = legacy5.download
_LEGACY_APPLY_BACKGROUND_TREATMENT = legacy5.apply_background_treatment
_LEGACY_RESOLVE = legacy5.resolve

_DURATION_EPSILON = 0.05
_OUTPUT_TOLERANCE = 0.30


def _sync_legacy_overrides():
    """Propagate facade monkey-patches/overrides into the retained v5 layer."""
    for name in (
        "OUTPUT_DIR",
        "probe_video",
        "normalization_required",
        "analyze_caption_region",
        "sha256_file",
        "subprocess",
        "_media_duration_seconds",
    ):
        if name in globals():
            setattr(legacy5, name, globals()[name])
    legacy5.preflight = globals()["preflight"]
    legacy5.normalize_for_render = globals()["normalize_for_render"]
    legacy5.download = globals()["download"]
    legacy5.apply_background_treatment = globals()["apply_background_treatment"]


def preflight(url):
    _sync_legacy_overrides()
    return _LEGACY_PREFLIGHT(url)


def normalize_for_render(
    target,
    source_probe,
    target_width=TARGET_WIDTH,
    target_height=TARGET_HEIGHT,
    target_fps=TARGET_FPS,
):
    _sync_legacy_overrides()
    return _LEGACY_NORMALIZE_FOR_RENDER(
        target,
        source_probe,
        target_width,
        target_height,
        target_fps,
    )


def download(
    asset,
    rendition,
    target,
    target_width=TARGET_WIDTH,
    target_height=TARGET_HEIGHT,
    segment_duration_seconds=175,
):
    _sync_legacy_overrides()
    return _LEGACY_DOWNLOAD(
        asset,
        rendition,
        target,
        target_width,
        target_height,
        segment_duration_seconds,
    )


def apply_background_treatment(target, treatment, caption_score=None):
    _sync_legacy_overrides()
    return _LEGACY_APPLY_BACKGROUND_TREATMENT(target, treatment, caption_score)


def _media_duration_seconds(path):
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise RuntimeError("cannot determine background duration")
    try:
        value = float((process.stdout or "").strip())
    except (TypeError, ValueError):
        raise RuntimeError("background duration is invalid") from None
    if value <= 0:
        raise RuntimeError("background duration must be positive")
    return value


def _fit_range(treatment, required_output_duration, *, test_mode=False):
    start = float(treatment["segment_start_seconds"])
    duration = float(treatment["segment_duration_seconds"])
    output = float(required_output_duration)
    if output <= 0:
        raise RuntimeError("required background output duration must be positive")
    if test_mode and duration / output > FIT_PLAYBACK_RATE_MAX:
        duration = output * min(2.0, FIT_PLAYBACK_RATE_MAX)
    rate = duration / output
    if not FIT_PLAYBACK_RATE_MIN - 1e-9 <= rate <= FIT_PLAYBACK_RATE_MAX + 1e-9:
        raise RuntimeError(
            f"derived playback rate {rate:.6f}x violates "
            f"{FIT_PLAYBACK_RATE_MIN:.2f}-{FIT_PLAYBACK_RATE_MAX:.2f}x bounds"
        )
    return start, duration, rate


def apply_fit_to_short_treatment(
    target,
    treatment,
    required_output_duration,
    caption_score=None,
    *,
    test_mode=False,
):
    if treatment.get("mode") != FIT_TO_SHORT_MODE:
        raise RuntimeError("schema-v6 background treatment must use fit_to_short")
    target = Path(target)
    if not target.exists():
        raise RuntimeError("normalized background is missing before fit-to-short")

    media_duration = _media_duration_seconds(target)
    start, duration, rate = _fit_range(
        treatment, required_output_duration, test_mode=test_mode
    )
    if start < 0 or start + duration > media_duration + _DURATION_EPSILON:
        raise RuntimeError("fit-to-short range exceeds resolved media duration")

    treated = target.parent / f"{target.name}.fit-to-short.mp4"
    treated.unlink(missing_ok=True)
    filters = [
        f"trim=start={start:.6f}:duration={duration:.6f}",
        f"setpts=(PTS-STARTPTS)/{rate:.10f}",
        f"fps={TARGET_FPS}",
        "format=yuv420p",
    ]
    started = time.monotonic()
    try:
        process = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-v", "error",
                "-i", str(target), "-map", "0:v:0", "-vf", ",".join(filters),
                "-an", "-sn", "-dn", "-map_metadata", "-1",
                "-c:v", "libx264", "-preset", NORMALIZED_PRESET,
                "-crf", str(NORMALIZED_CRF), "-movflags", "+faststart", str(treated),
            ],
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            raise RuntimeError(
                "fit-to-short treatment failed: "
                f"{(process.stderr or 'unknown failure').strip()[-1000:]}"
            )
        if not treated.exists() or treated.stat().st_size < 10000:
            raise RuntimeError("fit-to-short background is suspiciously small")
        probe = probe_video(treated)
        if normalization_required(probe):
            raise RuntimeError(
                f"fit-to-short output left production-normalized shape: {probe}"
            )
        actual_duration = _media_duration_seconds(treated)
        required = float(required_output_duration)
        if actual_duration + _OUTPUT_TOLERANCE < required:
            raise RuntimeError(
                "fit-to-short output is shorter than required render timeline"
            )
        if actual_duration > required + _OUTPUT_TOLERANCE:
            raise RuntimeError(
                "fit-to-short output duration exceeds deterministic tolerance"
            )
        treated.replace(target)
    finally:
        treated.unlink(missing_ok=True)

    readability = analyze_caption_region(
        target,
        min(actual_duration, float(required_output_duration)),
        caption_score,
    )
    return {
        "background_treatment_mode": FIT_TO_SHORT_MODE,
        "background_treatment_applied": True,
        "background_treatment_duration_seconds": round(time.monotonic() - started, 6),
        "background_treatment_input_duration_seconds": round(media_duration, 6),
        "background_treatment_segment_start_seconds": round(start, 6),
        "background_treatment_segment_duration_seconds": round(duration, 6),
        "background_treatment_required_output_seconds": round(
            float(required_output_duration), 6
        ),
        "background_treatment_derived_playback_rate": round(rate, 8),
        "background_treatment_output_duration_seconds": round(actual_duration, 6),
        "background_treatment_loop_mode": "none",
        "background_treatment_loop_count": 0,
        "background_treatment_test_subrange": bool(
            test_mode
            and abs(duration - float(treatment["segment_duration_seconds"])) > 1e-6
        ),
        "treated_background_bytes": target.stat().st_size,
        "treated_background_sha256": sha256_file(target),
        "readability": readability,
    }


def resolve(request_path, registry_path=None, do_download=True, do_preflight=True):
    _sync_legacy_overrides()
    result = _LEGACY_RESOLVE(
        request_path,
        registry_path,
        do_download=do_download,
        do_preflight=do_preflight,
    )
    request = load_json(request_path)
    if request.get("schema_version") != 6:
        return result

    registry = load_registry(
        registry_path or BASE / "media-library" / "backgrounds.json"
    )
    selected_asset = next(
        (
            item
            for item in registry.get("assets", [])
            if item.get("id") == result.get("background_asset_id")
        ),
        None,
    )
    if not isinstance(selected_asset, dict):
        raise RuntimeError("resolved schema-v6 background disappeared from registry")
    caption_score = selected_asset.get("caption_readability_score")
    try:
        caption_score_value = float(caption_score)
    except (TypeError, ValueError):
        raise RuntimeError(
            "schema-v6 background requires registered caption readability score"
        ) from None

    slot = result["background_selection"]
    treatment = treatment_for_slot(request, slot)
    result["background_treatment"] = treatment
    result["background_treatment_pending"] = True
    result["background_caption_readability_score"] = caption_score_value
    if do_download:
        metrics = result.setdefault("metrics", {})
        metrics["background_treatment_mode"] = FIT_TO_SHORT_MODE
        metrics["background_treatment_loop_mode"] = "none"
        metrics["background_treatment_loop_count"] = 0
    atomic_write_json(OUTPUT_DIR / "background_selection.json", result)
    print(
        "Deferred fit-to-short treatment "
        f"slot={slot} start={treatment['segment_start_seconds']:.3f}s "
        f"duration={treatment['segment_duration_seconds']:.3f}s"
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--registry")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    args = parser.parse_args()
    try:
        resolve(
            args.request,
            args.registry,
            do_download=not args.no_download,
            do_preflight=not args.skip_preflight,
        )
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
