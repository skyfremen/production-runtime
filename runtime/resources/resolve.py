"""Current background resolver with schema-v7 ordered no-loop sequences.

Schema v4-v6 execution is delegated unchanged to the frozen v6 resolver. Schema
v7 resolves every clip in one frozen primary sequence (or the entire frozen backup
sequence on failure), trims/concatenates the sequence once, and defers only the
overall speed adjustment until exact render duration is known.
"""
import argparse
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from base.contract import atomic_write_json, load_json
from guard.schema import (
    CONCATENATED_FIT_TO_SHORT_MODE,
    FIT_PLAYBACK_RATE_MAX,
    FIT_PLAYBACK_RATE_MIN,
    sequence_for_slot,
)
from resources import resolve_v6 as legacy6
from resources.resolve_v6 import *

# Preserve underscore-prefixed compatibility surface used by existing tests.
_fit_range = legacy6._fit_range
apply_fit_to_short_treatment = legacy6.apply_fit_to_short_treatment
_media_duration_seconds = legacy6._media_duration_seconds
_strip_epsilon = 0.05
_OUTPUT_TOLERANCE = 0.30


def _asset_map(registry):
    return {str(item.get("id")): item for item in registry.get("assets", []) if isinstance(item, dict)}


def _resolve_one(asset, target, segment, *, do_download, do_preflight):
    candidates = [(item, False) for item in suitable_renditions(asset)]
    fallback = generic_fallback(asset)
    if fallback:
        candidates.append((fallback, True))
    failures = []
    for rendition, generic in candidates:
        if do_preflight:
            ok, detail, preflight_seconds = preflight(rendition["direct_url"])
            if not ok:
                failures.append(f"rendition {rendition.get('id')}: {detail}")
                continue
        else:
            detail, preflight_seconds = "registry-only resolution; network preflight skipped", 0.0
        metrics = {}
        if do_download:
            try:
                metrics = download(
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
            media_duration = _media_duration_seconds(target)
            start = float(segment["segment_start_seconds"])
            duration = float(segment["segment_duration_seconds"])
            if start < 0 or start + duration > media_duration + _strip_epsilon:
                failures.append(
                    f"rendition {rendition.get('id')}: frozen range exceeds resolved media duration"
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
        }
    raise RuntimeError(
        f"background {asset.get('id')} has no executable rendition: " + "; ".join(failures)
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
            f"setpts=PTS-STARTPTS,fps={TARGET_FPS},format=yuv420p[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(
        "".join(labels)
        + f"concat=n={len(sequence)}:v=1:a=0,fps={TARGET_FPS},format=yuv420p[outv]"
    )
    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[outv]", "-an", "-sn", "-dn", "-map_metadata", "-1",
        "-c:v", "libx264", "-preset", NORMALIZED_PRESET, "-crf", str(NORMALIZED_CRF),
        "-movflags", "+faststart", str(target),
    ])
    started = time.monotonic()
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        raise RuntimeError(
            "background sequence concatenation failed: "
            f"{(process.stderr or 'unknown failure').strip()[-1000:]}"
        )
    if not target.exists() or target.stat().st_size < 10000:
        raise RuntimeError("concatenated background is suspiciously small")
    actual = _media_duration_seconds(target)
    expected = sum(float(x["segment_duration_seconds"]) for x in sequence)
    if actual + 0.30 < expected or actual > expected + 0.30:
        raise RuntimeError("concatenated background duration differs from frozen sequence")
    return {
        "background_sequence_assembly_duration_seconds": round(time.monotonic() - started, 6),
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
    used_duration = source_duration
    test_subrange = False
    if test_mode and used_duration / output > FIT_PLAYBACK_RATE_MAX:
        used_duration = output * min(2.0, FIT_PLAYBACK_RATE_MAX)
        test_subrange = True
    rate = used_duration / output
    if not FIT_PLAYBACK_RATE_MIN - 1e-9 <= rate <= FIT_PLAYBACK_RATE_MAX + 1e-9:
        raise RuntimeError(
            f"derived sequence playback rate {rate:.6f}x violates "
            f"{FIT_PLAYBACK_RATE_MIN:.2f}-{FIT_PLAYBACK_RATE_MAX:.2f}x bounds"
        )
    treated = target.parent / f"{target.name}.sequence-fit.mp4"
    treated.unlink(missing_ok=True)
    filters = []
    if test_subrange:
        filters.append(f"trim=start=0:duration={used_duration:.6f}")
    filters.extend([f"setpts=(PTS-STARTPTS)/{rate:.10f}", f"fps={TARGET_FPS}", "format=yuv420p"])
    started = time.monotonic()
    try:
        process = subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-v", "error", "-i", str(target),
            "-map", "0:v:0", "-vf", ",".join(filters), "-an", "-sn", "-dn",
            "-map_metadata", "-1", "-c:v", "libx264", "-preset", NORMALIZED_PRESET,
            "-crf", str(NORMALIZED_CRF), "-movflags", "+faststart", str(treated),
        ], capture_output=True, text=True)
        if process.returncode != 0:
            raise RuntimeError(
                "sequence fit-to-short treatment failed: "
                f"{(process.stderr or 'unknown failure').strip()[-1000:]}"
            )
        actual = _media_duration_seconds(treated)
        if actual + _OUTPUT_TOLERANCE < output or actual > output + _OUTPUT_TOLERANCE:
            raise RuntimeError("sequence fit-to-short output duration violates tolerance")
        treated.replace(target)
    finally:
        treated.unlink(missing_ok=True)
    readability = analyze_caption_region(target, min(actual, output), caption_score)
    return {
        "background_treatment_mode": CONCATENATED_FIT_TO_SHORT_MODE,
        "background_treatment_applied": True,
        "background_treatment_duration_seconds": round(time.monotonic() - started, 6),
        "background_treatment_input_duration_seconds": round(source_duration, 6),
        "background_treatment_source_duration_used_seconds": round(used_duration, 6),
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
    if request.get("schema_version") != 7:
        return legacy6.resolve(
            request_path, registry_path, do_download=do_download, do_preflight=do_preflight
        )

    resolution_started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    registry = load_registry(registry_path or BASE / "media-library" / "backgrounds.json")
    mapping = _asset_map(registry)
    failures = []
    for slot in ("primary", "backup"):
        sequence = sequence_for_slot(request, slot)
        resolved = []
        try:
            for index, segment in enumerate(sequence):
                asset_id = segment["background_id"]
                asset = mapping.get(asset_id)
                if not isinstance(asset, dict):
                    raise RuntimeError(f"unknown background asset {asset_id}")
                if asset.get("status") != "active" or asset.get("verified") is not True:
                    raise RuntimeError(f"background {asset_id} must be active and verified")
                path = OUTPUT_DIR / f"background.segment.{index}.asset"
                info = _resolve_one(
                    asset, path, segment,
                    do_download=do_download, do_preflight=do_preflight,
                )
                info["path"] = path
                resolved.append(info)
            assembly_metrics = {}
            if do_download:
                assembly_metrics = _assemble_sequence(resolved, sequence, OUTPUT_DIR / "background.asset")
            evidence = []
            for segment, info in zip(sequence, resolved):
                metrics = info["download_metrics"]
                evidence.append({
                    **segment,
                    "source": info["asset"].get("source"),
                    "source_page": info["asset"].get("source_page"),
                    "provider_asset_id": info["asset"].get("provider_asset_id"),
                    "rendition": info["rendition"],
                    "normalized_sha256": metrics.get("render_background_sha256"),
                    "normalized_bytes": metrics.get("render_background_bytes"),
                })
            first = resolved[0]
            result = {
                "requested_primary_sequence": request["visual"]["background_primary_sequence"],
                "requested_backup_sequence": request["visual"]["background_backup_sequence"],
                "background_asset_id": sequence[0]["background_id"],
                "background_selection": slot,
                "background_sequence": sequence,
                "background_sequence_evidence": evidence,
                "source": "multi",
                "source_page": first["asset"].get("source_page"),
                "creator": first["asset"].get("creator"),
                "license": first["asset"].get("license"),
                "commercial_use": True,
                "attribution_required": False,
                "rendition": first["rendition"],
                "target": {"width": TARGET_WIDTH, "height": TARGET_HEIGHT, "fps": TARGET_FPS},
                "rendition_fallback_used": any(x["generic_source_fallback_used"] for x in resolved),
                "logical_fallback_used": slot == "backup",
                "generic_source_fallback_used": any(x["generic_source_fallback_used"] for x in resolved),
                "background_treatment": {"mode": CONCATENATED_FIT_TO_SHORT_MODE, "sequence": sequence},
                "background_treatment_pending": bool(do_download),
                "failures_before_selection": list(failures),
                "metrics": {
                    "resolution_started_at": resolution_started_at.isoformat(),
                    "background_resolution_duration_seconds": round(time.monotonic() - started, 6),
                    "background_treatment_mode": CONCATENATED_FIT_TO_SHORT_MODE,
                    "background_treatment_loop_mode": "none",
                    "background_treatment_loop_count": 0,
                    **assembly_metrics,
                },
            }
            atomic_write_json(OUTPUT_DIR / "background_selection.json", result)
            return result
        except RuntimeError as exc:
            failures.append(f"{slot}: {exc}")
            for item in resolved:
                Path(item["path"]).unlink(missing_ok=True)
            (OUTPUT_DIR / "background.asset").unlink(missing_ok=True)
    raise RuntimeError("Both frozen background sequences failed: " + "; ".join(failures))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--registry")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    args = parser.parse_args()
    try:
        resolve(args.request, args.registry, do_download=not args.no_download, do_preflight=not args.skip_preflight)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
