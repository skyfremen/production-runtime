"""Production-only logical-background rendition resolver.

The immutable request decides *which* logical primary and backup may be used.
This module only chooses and downloads a physical rendition of those IDs.
Production is intentionally capped at a 1080p-equivalent source budget so a 4K
provider file is never downloaded just to render a 720x1280 Short.
"""

import argparse
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from media.background_policy import (
    TARGET_FPS,
    TARGET_HEIGHT,
    TARGET_WIDTH,
    rendition_is_production_suitable,
    rendition_sort_key,
)
from media.validate_media_library import load_registry, validate_request_backgrounds
from common.workflow_common import OUTPUT_DIR, atomic_write_json, load_json

BASE = Path(__file__).resolve().parents[1]
NORMALIZED_VIDEO_CODEC = "h264"
NORMALIZED_PRESET = "ultrafast"
NORMALIZED_CRF = 18
HTTP_HEADERS = {
    "User-Agent": "WackyDramasMediaResolver/1.0",
    "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.1",
}


def parse_rate(value):
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        try:
            denominator = float(denominator)
            return float(numerator) / denominator if denominator else 0.0
        except ValueError:
            return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0



def suitable_renditions(asset, target_width=TARGET_WIDTH, target_height=TARGET_HEIGHT, target_fps=TARGET_FPS):
    candidates = [
        dict(rendition)
        for rendition in asset.get("renditions", [])
        if rendition_is_production_suitable(
            rendition, target_width=target_width, target_height=target_height
        )
    ]
    return sorted(
        candidates,
        key=lambda rendition: rendition_sort_key(
            rendition, target_width, target_height, target_fps
        ),
    )


def select_best_rendition(asset, target_width=TARGET_WIDTH, target_height=TARGET_HEIGHT, target_fps=TARGET_FPS):
    candidates = suitable_renditions(asset, target_width, target_height, target_fps)
    return candidates[0] if candidates else None


def generic_fallback(asset):
    """Use the provider original only when it already fits the production cost cap.

    A generic provider download endpoint can resolve to a 4K file, so unknown or
    oversized originals are deliberately not used by production.
    """
    url = str(asset.get("direct_url") or "").strip()
    if not url:
        return None
    fallback = {
        "id": "generic-original-fallback",
        "width": asset.get("width"),
        "height": asset.get("height"),
        "fps": asset.get("fps"),
        "file_type": "video/mp4",
        "quality": "original",
        "direct_url": url,
    }
    return fallback if rendition_is_production_suitable(fallback) else None


def preflight(url):
    started = time.monotonic()
    request = urllib.request.Request(
        str(url), headers={**HTTP_HEADERS, "Range": "bytes=0-0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            # Some origins ignore Range. Read one byte only and close so preflight
            # can never become an accidental full background download.
            response.read(1)
            code = int(response.status)
            content_type = str(response.headers.get_content_type() or "").lower()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        elapsed = round(time.monotonic() - started, 6)
        return False, f"HTTP preflight failed: {exc}", elapsed
    elapsed = round(time.monotonic() - started, 6)
    if not 200 <= code < 300:
        return False, f"HTTP {code}", elapsed
    if content_type == "text/html":
        return False, "returned HTML instead of video media", elapsed
    return True, f"HTTP {code}, {content_type or 'unknown content-type'}", elapsed


def probe_video(target):
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate",
            "-of", "json", str(target),
        ],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise RuntimeError("download is not a decodable video")
    try:
        stream = json.loads(probe.stdout or "{}").get("streams", [])[0]
    except (IndexError, json.JSONDecodeError):
        raise RuntimeError("download has no video stream")
    if stream.get("codec_type") != "video":
        raise RuntimeError("download has no video stream")
    return {
        "codec": stream.get("codec_name"),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": round(parse_rate(stream.get("r_frame_rate")), 6),
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalization_required(probe, target_width=TARGET_WIDTH, target_height=TARGET_HEIGHT, target_fps=TARGET_FPS):
    """Return whether repeated rendering would decode or scale avoidable pixels."""
    return not (
        int(probe.get("width") or 0) == int(target_width)
        and int(probe.get("height") or 0) == int(target_height)
        and abs(float(probe.get("fps") or 0) - float(target_fps)) <= 0.05
        and probe.get("codec") == NORMALIZED_VIDEO_CODEC
    )


def normalize_for_render(target, source_probe, target_width=TARGET_WIDTH, target_height=TARGET_HEIGHT, target_fps=TARGET_FPS):
    """Normalize one modest source clip once, avoiding repeated scaling while looping."""
    target = Path(target)
    if not normalization_required(source_probe, target_width, target_height, target_fps):
        return {
            "background_normalization_applied": False,
            "background_normalization_duration_seconds": 0.0,
            "render_probe": source_probe,
        }

    normalized = target.parent / f"{target.name}.normalized.mp4"
    normalized.unlink(missing_ok=True)
    started = time.monotonic()
    try:
        process = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-v", "error", "-i", str(target),
                "-map", "0:v:0", "-vf",
                (
                    f"fps={target_fps},"
                    f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                    f"crop={target_width}:{target_height},format=yuv420p"
                ),
                "-an", "-sn", "-dn", "-map_metadata", "-1",
                "-c:v", "libx264", "-preset", NORMALIZED_PRESET,
                "-crf", str(NORMALIZED_CRF), "-movflags", "+faststart", str(normalized),
            ],
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            detail = (process.stderr or "unknown ffmpeg failure").strip()[-1000:]
            raise RuntimeError(f"background normalization failed: {detail}")
        if not normalized.exists() or normalized.stat().st_size < 10000:
            raise RuntimeError("normalized background is suspiciously small")
        render_probe = probe_video(normalized)
        if normalization_required(render_probe, target_width, target_height, target_fps):
            raise RuntimeError(f"normalized background has unexpected probe: {render_probe}")
        normalized.replace(target)
    finally:
        normalized.unlink(missing_ok=True)

    return {
        "background_normalization_applied": True,
        "background_normalization_duration_seconds": round(
            time.monotonic() - started, 6
        ),
        "render_probe": render_probe,
    }


def download(asset, rendition, target, target_width=TARGET_WIDTH, target_height=TARGET_HEIGHT):
    # Fail before network I/O if a caller somehow passes an oversized/UHD rendition.
    if not rendition_is_production_suitable(
        rendition, target_width=target_width, target_height=target_height
    ):
        raise RuntimeError(
            f"rendition {rendition.get('id')} is outside the production source budget"
        )

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    last_error = None
    for attempt in range(1, 4):
        target.unlink(missing_ok=True)
        request = urllib.request.Request(
            str(rendition["direct_url"]), headers=HTTP_HEADERS
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                if not 200 <= int(response.status) < 300:
                    raise RuntimeError(f"HTTP {response.status}")
                with target.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            last_error = None
            break
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2)
    if last_error is not None:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"download failed after 3 attempts: {last_error}")
    elapsed = round(time.monotonic() - started, 6)
    if not target.exists() or target.stat().st_size < 10000:
        raise RuntimeError(f"Downloaded background {asset['id']} is suspiciously small")
    probe = probe_video(target)
    if not rendition_is_production_suitable(
        {"file_type": "video/mp4", **probe},
        target_width=target_width,
        target_height=target_height,
    ):
        raise RuntimeError(
            f"downloaded rendition {probe['width']}x{probe['height']} is outside the production source budget"
        )
    source_bytes = target.stat().st_size
    source_sha256 = sha256_file(target)
    normalization = normalize_for_render(
        target, probe, target_width, target_height, TARGET_FPS
    )
    return {
        "downloaded_bytes": source_bytes,
        "download_duration_seconds": elapsed,
        "background_sha256": source_sha256,
        "render_background_bytes": target.stat().st_size,
        "render_background_sha256": sha256_file(target),
        "source_probe": probe,
        **normalization,
    }


def resolve(request_path, registry_path=None, do_download=True, do_preflight=True):
    resolution_started_at = datetime.now(timezone.utc)
    timer_started = time.monotonic()
    request = load_json(request_path)
    registry = load_registry(registry_path or BASE / "media-library" / "backgrounds.json")
    primary, backup = validate_request_backgrounds(request, registry)
    target = {"width": TARGET_WIDTH, "height": TARGET_HEIGHT, "fps": TARGET_FPS}
    failures = []
    for selection, asset in (("primary", primary), ("backup", backup)):
        registered = suitable_renditions(
            asset, target["width"], target["height"], target["fps"]
        )
        candidates = [(rendition, False) for rendition in registered]
        fallback = generic_fallback(asset)
        if fallback:
            candidates.append((fallback, True))
        if not candidates:
            failures.append(
                f"{selection} {asset['id']}: no production-suitable <=1080p rendition"
            )
            continue
        physical_failures = 0
        for rendition, is_generic in candidates:
            if do_preflight:
                ok, detail, preflight_seconds = preflight(rendition["direct_url"])
                if not ok:
                    failures.append(
                        f"{selection} {asset['id']} rendition {rendition['id']}: {detail}"
                    )
                    physical_failures += 1
                    continue
            else:
                detail, preflight_seconds = (
                    "registry-only resolution; network preflight skipped", 0.0
                )
            metrics = {}
            if do_download:
                try:
                    metrics = download(
                        asset,
                        rendition,
                        OUTPUT_DIR / "background.asset",
                        target["width"],
                        target["height"],
                    )
                except (subprocess.CalledProcessError, RuntimeError) as exc:
                    failures.append(
                        f"{selection} {asset['id']} rendition {rendition['id']}: full download failed: {exc}"
                    )
                    physical_failures += 1
                    continue
            recorded_rendition = dict(rendition)
            if metrics.get("source_probe"):
                recorded_rendition.update({
                    "width": metrics["source_probe"]["width"],
                    "height": metrics["source_probe"]["height"],
                    "fps": metrics["source_probe"]["fps"],
                    "codec": metrics["source_probe"]["codec"],
                })
            recorded_rendition["selection_reason"] = (
                "controlled_generic_original_fallback"
                if is_generic
                else "next_production_suitable_rendition_after_failure"
                if physical_failures
                else "lowest_cost_production_suitable_rendition"
            )
            result = {
                "requested_primary_id": primary["id"],
                "requested_backup_id": backup["id"],
                "background_asset_id": asset["id"],
                "background_selection": selection,
                "source": asset["source"],
                "source_page": asset["source_page"],
                "creator": asset.get("creator"),
                "license": asset["license"],
                "commercial_use": asset.get("commercial_use"),
                "attribution_required": asset.get("attribution_required"),
                "rendition": recorded_rendition,
                "direct_url": recorded_rendition["direct_url"],
                "target": target,
                "rendition_fallback_used": physical_failures > 0,
                "logical_fallback_used": selection == "backup",
                "generic_source_fallback_used": is_generic,
                "preflight": detail,
                "failures_before_selection": list(failures),
                "metrics": {
                    "resolution_started_at": resolution_started_at.isoformat(),
                    "preflight_duration_seconds": preflight_seconds,
                    "background_resolution_duration_seconds": round(
                        time.monotonic() - timer_started, 6
                    ),
                    **metrics,
                },
            }
            atomic_write_json(OUTPUT_DIR / "background_selection.json", result)
            print(
                f"Selected {selection} background {asset['id']} rendition {rendition['id']} "
                f"({rendition.get('width')}x{rendition.get('height')}): {detail}; "
                f"generic={is_generic}; physical_fallbacks={physical_failures}"
            )
            return result
    raise RuntimeError(
        "Both requested background choices failed rendition resolution: "
        + "; ".join(failures)
    )


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

