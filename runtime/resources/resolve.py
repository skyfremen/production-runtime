"""V1 three-clip no-loop background resolver using the proven rendition engine."""
import argparse
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from base.contract import OUTPUT_DIR, atomic_write_json, load_json
from resources import media
from resources.media import (
    NORMALIZED_CRF,
    NORMALIZED_PRESET,
    TARGET_FPS,
    TARGET_HEIGHT,
    TARGET_WIDTH,
    analyze_caption_region,
    generic_fallback,
    sha256_file,
    suitable_renditions,
)
from resources.validate import asset_map, load_registry, validate_request_backgrounds

FIT_MIN = 1.0
FIT_MAX = 2.5
MODE = "concatenated_fit_to_short"
EPS = 0.05
TOLERANCE = 0.30


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
    if process.returncode:
        raise RuntimeError("cannot determine background duration")
    try:
        value = float((process.stdout or "").strip())
    except (TypeError, ValueError):
        raise RuntimeError("background duration is invalid") from None
    if value <= 0:
        raise RuntimeError("background duration must be positive")
    return value


def _resolve_one(asset, target, segment, *, do_download, do_preflight):
    candidates = [(item, False) for item in suitable_renditions(asset)]
    fallback = generic_fallback(asset)
    if fallback:
        candidates.append((fallback, True))
    failures = []
    for rendition, generic in candidates:
        if do_preflight:
            ok, detail, preflight_seconds = media.preflight(rendition["direct_url"])
            if not ok:
                failures.append(f"rendition {rendition.get('id')}: {detail}")
                continue
        else:
            detail, preflight_seconds = "registry-only; network preflight skipped", 0.0
        metrics = {}
        if do_download:
            try:
                metrics = media.download(
                    asset,
                    rendition,
                    target,
                    TARGET_WIDTH,
                    TARGET_HEIGHT,
                    float(segment["segment_duration_seconds"]),
                )
            except (subprocess.CalledProcessError, RuntimeError) as exc:
                failures.append(f"rendition {rendition.get('id')}: {exc}")
                continue
            duration = _media_duration_seconds(target)
            start = float(segment["segment_start_seconds"])
            length = float(segment["segment_duration_seconds"])
            if start < 0 or start + length > duration + EPS:
                failures.append(
                    f"rendition {rendition.get('id')}: selected range exceeds media"
                )
                Path(target).unlink(missing_ok=True)
                continue
        recorded = dict(rendition)
        if metrics.get("source_probe"):
            recorded.update(metrics["source_probe"])
        return {
            "asset": asset,
            "rendition": recorded,
            "generic_source_fallback_used": generic,
            "preflight": detail,
            "preflight_duration_seconds": preflight_seconds,
            "download_metrics": metrics,
            "path": Path(target),
        }
    raise RuntimeError(
        f"background {asset.get('id')} has no executable rendition: "
        + "; ".join(failures)
    )


def _assemble_sequence(resolved, sequence, target):
    target = Path(target)
    target.unlink(missing_ok=True)
    command = ["ffmpeg", "-y", "-hide_banner", "-v", "error"]
    for item in resolved:
        command.extend(["-i", str(item["path"])])
    filters = []
    labels = []
    for index, segment in enumerate(sequence):
        start = float(segment["segment_start_seconds"])
        duration = float(segment["segment_duration_seconds"])
        label = f"v{index}"
        filters.append(
            f"[{index}:v:0]trim=start={start:.6f}:duration={duration:.6f},"
            f"setpts=PTS-STARTPTS,fps={TARGET_FPS},setsar=1,format=yuv420p[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(
        "".join(labels)
        + f"concat=n={len(sequence)}:v=1:a=0,fps={TARGET_FPS},setsar=1,format=yuv420p[outv]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-c:v",
            "libx264",
            "-preset",
            NORMALIZED_PRESET,
            "-crf",
            str(NORMALIZED_CRF),
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(target),
        ]
    )
    started = time.monotonic()
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError(
            "background concatenation failed: "
            + (process.stderr or "unknown failure").strip()[-1000:]
        )
    if not target.exists() or target.stat().st_size < 10000:
        raise RuntimeError("concatenated background is suspiciously small")
    actual = _media_duration_seconds(target)
    expected = sum(float(item["segment_duration_seconds"]) for item in sequence)
    if actual + TOLERANCE < expected or actual > expected + TOLERANCE:
        raise RuntimeError("concatenated background duration differs from frozen sequence")
    return {
        "background_sequence_assembly_duration_seconds": round(
            time.monotonic() - started, 6
        ),
        "background_sequence_source_duration_seconds": round(expected, 6),
        "background_sequence_output_duration_seconds": round(actual, 6),
        "background_sequence_sha256": sha256_file(target),
        "background_sequence_bytes": target.stat().st_size,
    }


def apply_concatenated_fit_to_short_treatment(
    target, required_output_duration, caption_score=None, *, test_mode=False
):
    target = Path(target)
    if not target.exists():
        raise RuntimeError("concatenated background is missing before fit-to-short")
    source_duration = _media_duration_seconds(target)
    output = float(required_output_duration)
    if output <= 0:
        raise RuntimeError("required background output duration must be positive")

    # The three frozen source segments can legitimately be longer than the final
    # narrated Short. Never exceed the visual speed cap just to consume every
    # source frame: deterministically trim the tail to the maximum duration that
    # can be played within the allowed rate. Test mode keeps its existing 2x cap.
    fit_limit = min(2.0, FIT_MAX) if test_mode else FIT_MAX
    max_source_duration = output * fit_limit
    used_duration = min(source_duration, max_source_duration)
    source_trimmed = used_duration < source_duration - 1e-9
    test_subrange = bool(test_mode and source_trimmed)
    rate = used_duration / output
    if not FIT_MIN - 1e-9 <= rate <= FIT_MAX + 1e-9:
        raise RuntimeError(
            f"derived background playback rate {rate:.6f}x violates "
            f"{FIT_MIN:.2f}-{FIT_MAX:.2f}x bounds"
        )
    treated = target.parent / f"{target.name}.sequence-fit.mp4"
    treated.unlink(missing_ok=True)
    filters = []
    if source_trimmed:
        filters.append(f"trim=start=0:duration={used_duration:.6f}")
    filters.extend(
        [
            f"setpts=(PTS-STARTPTS)/{rate:.10f}",
            f"fps={TARGET_FPS}",
            "setsar=1",
            "format=yuv420p",
        ]
    )
    started = time.monotonic()
    try:
        process = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-v",
                "error",
                "-i",
                str(target),
                "-map",
                "0:v:0",
                "-vf",
                ",".join(filters),
                "-an",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-c:v",
                "libx264",
                "-preset",
                NORMALIZED_PRESET,
                "-crf",
                str(NORMALIZED_CRF),
                "-movflags",
                "+faststart",
                str(treated),
            ],
            capture_output=True,
            text=True,
        )
        if process.returncode:
            raise RuntimeError(
                "background fit-to-short treatment failed: "
                + (process.stderr or "unknown failure").strip()[-1000:]
            )
        actual = _media_duration_seconds(treated)
        if actual + TOLERANCE < output or actual > output + TOLERANCE:
            raise RuntimeError("fit-to-short output duration violates tolerance")
        treated.replace(target)
    finally:
        treated.unlink(missing_ok=True)
    readability = analyze_caption_region(
        target, min(actual, output), caption_score
    )
    return {
        "background_treatment_mode": MODE,
        "background_treatment_applied": True,
        "background_treatment_duration_seconds": round(
            time.monotonic() - started, 6
        ),
        "background_treatment_input_duration_seconds": round(source_duration, 6),
        "background_treatment_source_duration_used_seconds": round(
            used_duration, 6
        ),
        "background_treatment_source_trimmed": source_trimmed,
        "background_treatment_source_trimmed_seconds": round(
            max(0.0, source_duration - used_duration), 6
        ),
        "background_treatment_required_output_seconds": round(output, 6),
        "background_treatment_derived_playback_rate": round(rate, 8),
        "background_treatment_output_duration_seconds": round(actual, 6),
        "background_treatment_loop_mode": "none",
        "background_treatment_loop_count": 0,
        "background_treatment_test_subrange": test_subrange,
        "treated_background_bytes": target.stat().st_size,
        "treated_background_sha256": sha256_file(target),
        "readability": readability,
    }


def resolve(request_path, registry_path=None, do_download=True, do_preflight=True):
    request = load_json(request_path)
    if request.get("request_version") != 1:
        raise RuntimeError("Only Wacky Dramas V1 requests are supported")
    registry = load_registry(registry_path)
    sequence = validate_request_backgrounds(request, registry)
    mapping = asset_map(registry)
    started_at = datetime.now(timezone.utc)
    timer = time.monotonic()
    resolved = []
    try:
        for index, segment in enumerate(sequence):
            asset_id = segment["background_id"]
            asset = mapping[asset_id]
            info = _resolve_one(
                asset,
                OUTPUT_DIR / f"background.segment.{index}.asset",
                segment,
                do_download=do_download,
                do_preflight=do_preflight,
            )
            resolved.append(info)
        assembly = {}
        if do_download:
            assembly = _assemble_sequence(
                resolved, sequence, OUTPUT_DIR / "background.asset"
            )
        evidence = []
        for segment, info in zip(sequence, resolved):
            evidence.append(
                {
                    **segment,
                    "source": info["asset"].get("source"),
                    "source_page": info["asset"].get("source_page"),
                    "provider_asset_id": info["asset"].get("provider_asset_id"),
                    "rendition": info["rendition"],
                    "normalized_sha256": info["download_metrics"].get(
                        "render_background_sha256"
                    ),
                    "normalized_bytes": info["download_metrics"].get(
                        "render_background_bytes"
                    ),
                }
            )
        result = {
            "background_selection": "selected",
            "background_sequence": sequence,
            "background_sequence_evidence": evidence,
            "background_treatment": {"mode": MODE, "sequence": sequence},
            "background_treatment_pending": bool(do_download),
            "logical_asset_ids": [x["background_id"] for x in sequence],
            "rendition_fallback_used": any(
                x["generic_source_fallback_used"] for x in resolved
            ),
            "metrics": {
                "resolution_started_at": started_at.isoformat(),
                "background_resolution_duration_seconds": round(
                    time.monotonic() - timer, 6
                ),
                "background_treatment_mode": MODE,
                "background_treatment_loop_mode": "none",
                "background_treatment_loop_count": 0,
                **assembly,
            },
        }
        atomic_write_json(OUTPUT_DIR / "background_selection.json", result)
        return result
    finally:
        if do_download:
            for item in resolved:
                item["path"].unlink(missing_ok=True)


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
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
