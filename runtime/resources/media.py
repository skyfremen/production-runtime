"""Physical background acquisition, fallback, probe, and normalization helpers."""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from base.contract import atomic_write_json
from resources.policy import (
    TARGET_FPS,
    TARGET_HEIGHT,
    TARGET_WIDTH,
    crop_fill_geometry,
    rendition_is_production_suitable,
)
from resources.quality import analyze_caption_region

NORMALIZED_VIDEO_CODEC = "h264"
NORMALIZED_PRESET = "ultrafast"
NORMALIZED_CRF = 18
HTTP_HEADERS = {
    "User-Agent": "RuntimeResourceClient/1.0",
    "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.1",
}
PEXELS_ID_RE = re.compile(r"^px-(\d+)$")


def parse_rate(value):
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    if "/" in raw:
        left, right = raw.split("/", 1)
        try:
            right = float(right)
            return float(left) / right if right else 0.0
        except ValueError:
            return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def pexels_fallback_url(asset):
    match = PEXELS_ID_RE.fullmatch(str(asset.get("id") or ""))
    if not match:
        return None
    return f"https://www.pexels.com/download/video/{match.group(1)}"


def preflight(url):
    started = time.monotonic()
    request = urllib.request.Request(
        str(url), headers={**HTTP_HEADERS, "Range": "bytes=0-0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            response.read(1)
            code = int(response.status)
            content_type = str(response.headers.get_content_type() or "").lower()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"HTTP preflight failed: {exc}", round(time.monotonic() - started, 6)
    elapsed = round(time.monotonic() - started, 6)
    if not 200 <= code < 300:
        return False, f"HTTP {code}", elapsed
    if content_type == "text/html":
        return False, "returned HTML instead of video media", elapsed
    return True, f"HTTP {code}, {content_type or 'unknown content-type'}", elapsed


def probe_video(target):
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,r_frame_rate:format=duration",
            "-of",
            "json",
            str(target),
        ],
        capture_output=True,
        text=True,
    )
    if process.returncode:
        raise RuntimeError("download is not a decodable video")
    try:
        payload = json.loads(process.stdout or "{}")
        stream = payload.get("streams", [])[0]
        duration = float(payload.get("format", {}).get("duration"))
    except (IndexError, TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("download has incomplete video metadata") from None
    if stream.get("codec_type") != "video":
        raise RuntimeError("download has no video stream")
    if duration <= 0:
        raise RuntimeError("download has invalid duration")
    return {
        "codec": stream.get("codec_name"),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": round(parse_rate(stream.get("r_frame_rate")), 6),
        "duration_seconds": round(duration, 6),
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalization_required(
    probe,
    target_width=TARGET_WIDTH,
    target_height=TARGET_HEIGHT,
    target_fps=TARGET_FPS,
):
    return not (
        int(probe.get("width") or 0) == int(target_width)
        and int(probe.get("height") or 0) == int(target_height)
        and abs(float(probe.get("fps") or 0) - float(target_fps)) <= 0.05
        and probe.get("codec") == NORMALIZED_VIDEO_CODEC
    )


def normalize_for_render(
    target,
    source_probe,
    target_width=TARGET_WIDTH,
    target_height=TARGET_HEIGHT,
    target_fps=TARGET_FPS,
):
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
                f"fps={target_fps},scale={target_width}:{target_height}:force_original_aspect_ratio=increase,crop={target_width}:{target_height},format=yuv420p",
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
                str(normalized),
            ],
            capture_output=True,
            text=True,
        )
        if process.returncode:
            raise RuntimeError(
                "background normalization failed: "
                + (process.stderr or "unknown")[-1000:]
            )
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


def download(
    asset,
    url,
    target,
    target_width=TARGET_WIDTH,
    target_height=TARGET_HEIGHT,
    segment_duration_seconds=175,
    media_source="registry_download_url",
):
    url = str(url or "").strip()
    if not url:
        raise RuntimeError("background media URL is empty")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    cache_root = Path(os.getenv("RUNTIME_RESOURCE_CACHE", "/tmp/runtime-resource-cache"))
    cache_root.mkdir(parents=True, exist_ok=True)
    identity = json.dumps(
        [asset.get("id"), url, target_width, target_height, TARGET_FPS],
        separators=(",", ":"),
    )
    key = hashlib.sha256(identity.encode()).hexdigest()
    cache_video = cache_root / f"{key}.mp4"
    cache_meta = cache_root / f"{key}.json"
    lock = (cache_root / f"{key}.lock").open("a+b")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    try:
        if cache_video.exists() and cache_meta.exists() and cache_video.stat().st_size >= 10000:
            cached_probe = probe_video(cache_video)
            if not normalization_required(cached_probe, target_width, target_height, TARGET_FPS):
                shutil.copy2(cache_video, target)
                cached = json.loads(cache_meta.read_text(encoding="utf-8"))
                cached.update(
                    {
                        "background_cache_hit": True,
                        "background_cache_key": key,
                        "media_source": media_source,
                        "source_url_used": url,
                        "render_background_bytes": target.stat().st_size,
                        "render_background_sha256": sha256_file(target),
                    }
                )
                return cached

        started = time.monotonic()
        last_error = None
        for attempt in range(1, 4):
            target.unlink(missing_ok=True)
            request = urllib.request.Request(url, headers=HTTP_HEADERS)
            try:
                with urllib.request.urlopen(request, timeout=180) as response, target.open(
                    "wb"
                ) as output:
                    if not 200 <= int(response.status) < 300:
                        raise RuntimeError(f"HTTP {response.status}")
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
        physical = {"file_type": "video/mp4", **probe}
        if not rendition_is_production_suitable(
            physical, target_width=target_width, target_height=target_height
        ):
            raise RuntimeError(
                f"downloaded media {probe['width']}x{probe['height']} cannot meet the post-crop quality floor"
            )

        source_bytes = target.stat().st_size
        source_sha = sha256_file(target)
        # Registry membership already represents editorial/readability approval.
        # Runtime sampling is retained only to choose adaptive caption protection.
        readability = analyze_caption_region(target, segment_duration_seconds)
        geometry = crop_fill_geometry(
            probe["width"], probe["height"], target_width, target_height
        )
        normalization = normalize_for_render(
            target, probe, target_width, target_height, TARGET_FPS
        )
        result = {
            "downloaded_bytes": source_bytes,
            "download_duration_seconds": elapsed,
            "background_sha256": source_sha,
            "render_background_bytes": target.stat().st_size,
            "render_background_sha256": sha256_file(target),
            "source_probe": probe,
            "effective_crop_width": round(geometry["effective_crop_width"], 3),
            "effective_crop_height": round(geometry["effective_crop_height"], 3),
            "upscale_factor": round(geometry["scale_factor"], 6),
            "significant_upscaling_used": geometry["scale_factor"] > 1.05,
            "readability": readability,
            "background_cache_hit": False,
            "background_cache_key": key,
            "media_source": media_source,
            "source_url_used": url,
            **normalization,
        }
        temp_cache = cache_root / f"{key}.{os.getpid()}.tmp"
        shutil.copy2(target, temp_cache)
        temp_cache.replace(cache_video)
        atomic_write_json(cache_meta, result)
        return result
    finally:
        lock.close()
