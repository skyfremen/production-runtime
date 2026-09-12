"""Physical background resolver with immutable schema-v5 treatment execution.

The retained resolve_base module owns the proven logical-ID/rendition/cache/
normalization path. This layer only applies the request-authorized temporal
segment and playback rate after the selected physical file is already normalized
to production size. The normalized cache therefore remains reusable and never
becomes persistent creative state.
"""

import argparse
import subprocess
import time
from pathlib import Path

from guard.schema import treatment_for_slot
from resources import resolve_base as resolve_impl
from resources.resolve_base import *  # re-export the established resolver surface

_original_preflight = resolve_impl.preflight
_original_normalize_for_render = resolve_impl.normalize_for_render
_original_download = resolve_impl.download


def _sync_base_overrides():
    """Preserve the established resolver's observable/patchable module surface."""
    resolve_impl.OUTPUT_DIR = OUTPUT_DIR
    resolve_impl.probe_video = globals()["probe_video"]
    resolve_impl.normalize_for_render = globals()["normalize_for_render"]
    resolve_impl.preflight = globals()["preflight"]
    resolve_impl.download = globals()["download"]


def preflight(url):
    _sync_base_overrides()
    return _original_preflight(url)


def normalize_for_render(
    target,
    source_probe,
    target_width=TARGET_WIDTH,
    target_height=TARGET_HEIGHT,
    target_fps=TARGET_FPS,
):
    _sync_base_overrides()
    return _original_normalize_for_render(
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
    _sync_base_overrides()
    return _original_download(
        asset,
        rendition,
        target,
        target_width,
        target_height,
        segment_duration_seconds,
    )


def _media_duration_seconds(path):
    process = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise RuntimeError("cannot determine normalized background duration")
    try:
        value = float((process.stdout or "").strip())
    except (TypeError, ValueError):
        raise RuntimeError("normalized background duration is invalid") from None
    if value <= 0:
        raise RuntimeError("normalized background duration must be positive")
    return value


def _validate_treatment_window(treatment, media_duration):
    start = float(treatment["segment_start_seconds"])
    duration = treatment["segment_duration_seconds"]
    rate = float(treatment["playback_rate"])
    if start < 0 or not 1.0 <= rate <= 2.0:
        raise RuntimeError("background treatment violates execution bounds")
    if duration is None:
        if start != 0:
            raise RuntimeError("full-source background treatment must start at zero")
        return
    duration = float(duration)
    if duration < 1.0:
        raise RuntimeError("background treatment segment is too short")
    if start >= media_duration or start + duration > media_duration + 0.05:
        raise RuntimeError(
            "background treatment segment exceeds the resolved media duration"
        )


def apply_background_treatment(target, treatment, caption_score=None):
    """Apply trim/speed once to an already normalized 1080x1920/30 asset."""
    target = Path(target)
    if not target.exists():
        raise RuntimeError("normalized background is missing before treatment")
    media_duration = _media_duration_seconds(target)
    _validate_treatment_window(treatment, media_duration)

    start = float(treatment["segment_start_seconds"])
    duration = treatment["segment_duration_seconds"]
    rate = float(treatment["playback_rate"])
    identity = duration is None and start == 0.0 and abs(rate - 1.0) <= 1e-9
    if identity:
        return {
            "background_treatment_applied": False,
            "background_treatment_duration_seconds": 0.0,
            "background_treatment_input_duration_seconds": round(media_duration, 6),
            "background_treatment_output_duration_seconds": round(media_duration, 6),
            "treated_background_bytes": target.stat().st_size,
            "treated_background_sha256": sha256_file(target),
            "readability": analyze_caption_region(
                target, media_duration, caption_score
            ),
        }

    treated = target.parent / f"{target.name}.treated.mp4"
    treated.unlink(missing_ok=True)
    filters = []
    if duration is None:
        filters.append("trim=start=0")
        output_duration = media_duration / rate
    else:
        filters.append(
            f"trim=start={start:.6f}:duration={float(duration):.6f}"
        )
        output_duration = float(duration) / rate
    filters.extend([
        f"setpts=(PTS-STARTPTS)/{rate:.8f}",
        f"fps={TARGET_FPS}",
        "format=yuv420p",
    ])
    started = time.monotonic()
    try:
        process = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-v", "error",
                "-i", str(target),
                "-map", "0:v:0", "-vf", ",".join(filters),
                "-an", "-sn", "-dn", "-map_metadata", "-1",
                "-c:v", "libx264", "-preset", NORMALIZED_PRESET,
                "-crf", str(NORMALIZED_CRF), "-movflags", "+faststart",
                str(treated),
            ],
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            detail = (process.stderr or "unknown ffmpeg failure").strip()[-1000:]
            raise RuntimeError(f"background treatment failed: {detail}")
        if not treated.exists() or treated.stat().st_size < 10000:
            raise RuntimeError("treated background is suspiciously small")
        probe = probe_video(treated)
        if normalization_required(probe):
            raise RuntimeError(
                f"treated background left production-normalized shape: {probe}"
            )
        actual_duration = _media_duration_seconds(treated)
        if actual_duration <= 0 or actual_duration > output_duration + 0.25:
            raise RuntimeError("treated background duration is inconsistent")
        treated.replace(target)
    finally:
        treated.unlink(missing_ok=True)

    elapsed = round(time.monotonic() - started, 6)
    readability = analyze_caption_region(
        target, min(actual_duration, output_duration), caption_score
    )
    return {
        "background_treatment_applied": True,
        "background_treatment_duration_seconds": elapsed,
        "background_treatment_input_duration_seconds": round(media_duration, 6),
        "background_treatment_output_duration_seconds": round(actual_duration, 6),
        "treated_background_bytes": target.stat().st_size,
        "treated_background_sha256": sha256_file(target),
        "readability": readability,
    }


def resolve(request_path, registry_path=None, do_download=True, do_preflight=True):
    _sync_base_overrides()
    result = resolve_impl.resolve(
        request_path,
        registry_path,
        do_download=do_download,
        do_preflight=do_preflight,
    )
    request = load_json(request_path)
    if request.get("schema_version") != 5:
        return result

    slot = result["background_selection"]
    treatment = treatment_for_slot(request, slot)
    result["background_treatment"] = treatment
    if not do_download:
        atomic_write_json(OUTPUT_DIR / "background_selection.json", result)
        return result

    registry = load_registry(registry_path or BASE / "media-library" / "backgrounds.json")
    asset = next(
        (
            item for item in registry.get("assets", [])
            if item.get("id") == result["background_asset_id"]
        ),
        None,
    )
    if asset is None:
        raise RuntimeError("resolved logical background disappeared from registry")

    metrics = result.setdefault("metrics", {})
    metrics.setdefault("normalized_background_bytes", metrics.get("render_background_bytes"))
    metrics.setdefault("normalized_background_sha256", metrics.get("render_background_sha256"))
    treatment_metrics = apply_background_treatment(
        OUTPUT_DIR / "background.asset",
        treatment,
        asset.get("caption_readability_score"),
    )
    result["readability"] = treatment_metrics.pop("readability")
    metrics.update(treatment_metrics)
    metrics["render_background_bytes"] = metrics["treated_background_bytes"]
    metrics["render_background_sha256"] = metrics["treated_background_sha256"]
    atomic_write_json(OUTPUT_DIR / "background_selection.json", result)
    print(
        "Applied immutable background treatment "
        f"slot={slot} start={treatment['segment_start_seconds']:.3f}s "
        f"duration={treatment['segment_duration_seconds']} "
        f"rate={treatment['playback_rate']:.3f}x"
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
