import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone

from base.contract import (
    OUTPUT_DIR,
    PRODUCTION_MAX_SECONDS,
    atomic_write_json,
    env_bool,
    expected_video_config,
    load_json,
)

BLACKDETECT_MAX_ALLOWED_SECONDS = 0.75


def parse_rate(value):
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    if "/" in raw:
        num, den = raw.split("/", 1)
        try:
            den_value = float(den)
            return float(num) / den_value if den_value else 0.0
        except ValueError:
            return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def inline_blackdetect_max(render_meta):
    """Require black-detection evidence produced by the canonical render pass."""
    if render_meta.get("inline_blackdetect_passed") is not True:
        raise SystemExit(
            "Render verification failed: canonical inline blackdetect evidence is missing or failed"
        )
    try:
        maximum = float(render_meta["inline_blackdetect_max_duration_seconds"])
    except (KeyError, TypeError, ValueError):
        raise SystemExit(
            "Render verification failed: inline blackdetect maximum duration is missing or invalid"
        ) from None
    if maximum < 0 or maximum >= BLACKDETECT_MAX_ALLOWED_SECONDS:
        raise SystemExit(
            "Render verification failed: inline blackdetect metadata exceeds safety threshold"
        )
    return maximum


def main():
    verification_started = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request = load_json(args.request)
    video = OUTPUT_DIR / "short.mp4"
    metadata_path = OUTPUT_DIR / "render-metadata.json"
    if not video.exists() or video.stat().st_size < 100000:
        raise SystemExit(
            "Render verification failed: short.mp4 is missing or suspiciously small"
        )
    if not metadata_path.exists():
        raise SystemExit("Render verification failed: render-metadata.json is missing")

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate,profile,pix_fmt,color_space,color_transfer,color_primaries,sample_rate,profile",
            "-of",
            "json",
            str(video),
        ],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise SystemExit("Render verification failed: ffprobe could not read short.mp4")
    info = json.loads(probe.stdout or "{}")
    streams = info.get("streams", [])
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if len(videos) != 1:
        raise SystemExit(
            f"Render verification failed: expected one video stream, got {len(videos)}"
        )
    if len(audios) != 1:
        raise SystemExit(
            "Render verification failed: expected exactly one narration audio stream, "
            f"got {len(audios)}"
        )

    config = expected_video_config()
    video_stream = videos[0]
    actual_dimensions = (
        int(video_stream.get("width") or 0),
        int(video_stream.get("height") or 0),
    )
    expected_dimensions = (config["width"], config["height"])
    if actual_dimensions != expected_dimensions:
        raise SystemExit(
            "Render verification failed: expected "
            f"{config['width']}x{config['height']}, got "
            f"{video_stream.get('width')}x{video_stream.get('height')}"
        )
    actual_fps = parse_rate(video_stream.get("r_frame_rate"))
    if abs(actual_fps - float(config["fps"])) > 0.01:
        raise SystemExit(
            f"Render verification failed: expected {config['fps']} fps, got "
            f"{video_stream.get('r_frame_rate')} ({actual_fps:.3f})"
        )
    if video_stream.get("codec_name") != "h264":
        raise SystemExit(
            "Render verification failed: video codec must be h264, got "
            f"{video_stream.get('codec_name')}"
        )
    if str(video_stream.get("profile", "")).lower() != "high":
        raise SystemExit("Render verification failed: H.264 profile must be High")
    if video_stream.get("pix_fmt") != "yuv420p":
        raise SystemExit("Render verification failed: pixel format must be yuv420p")
    for field in ("color_space", "color_transfer", "color_primaries"):
        if video_stream.get(field) != "bt709":
            raise SystemExit(f"Render verification failed: {field} must be bt709")
    if audios[0].get("codec_name") != "aac":
        raise SystemExit(
            "Render verification failed: audio codec must be aac, got "
            f"{audios[0].get('codec_name')}"
        )
    if str(audios[0].get("profile", "")).upper() not in {"LC", "AAC LC"}:
        raise SystemExit("Render verification failed: AAC profile must be LC")
    if int(audios[0].get("sample_rate") or 0) != 48000:
        raise SystemExit("Render verification failed: audio sample rate must be 48000 Hz")

    duration = float(info.get("format", {}).get("duration") or 0)
    test_mode = env_bool("STORY_TEST_MODE", False)
    if test_mode:
        max_seconds = float(os.getenv("STORY_RENDER_MAX_SECONDS", "5"))
        if duration > max_seconds + 0.55:
            raise SystemExit(
                f"Render verification failed: test duration {duration:.3f}s exceeds "
                f"{max_seconds + 0.55:.3f}s tolerance"
            )
    elif duration > PRODUCTION_MAX_SECONDS:
        raise SystemExit(
            f"Render verification failed: production duration {duration:.3f}s exceeds "
            f"{PRODUCTION_MAX_SECONDS:.0f}s"
        )

    for fraction in (0.25, 0.50, 0.75):
        timestamp = max(0.05, duration * fraction)
        check = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            raise SystemExit(
                f"Render verification failed: frame at {timestamp:.2f}s cannot be decoded"
            )

    render_meta = load_json(metadata_path)
    blackdetect_max = inline_blackdetect_max(render_meta)

    if abs(float(render_meta["video_seconds"]) - duration) > 0.25:
        raise SystemExit("Render verification failed: metadata/video duration mismatch")
    render_meta.update(
        {
            "render_verified": True,
            "render_verified_at": datetime.now(timezone.utc).isoformat(),
            "render_verification_source": "ffprobe_frame_decode_and_inline_blackdetect",
            "render_verification_duration_seconds": round(
                time.monotonic() - verification_started, 6
            ),
            "verified_blackdetect_max_duration_seconds": round(blackdetect_max, 6),
            "configured_video_seconds": render_meta["video_seconds"],
            "video_seconds": duration,
            "fps": actual_fps,
            "video_codec": video_stream["codec_name"],
            "audio_codec": audios[0]["codec_name"],
            "audio_stream_count": len(audios),
            "h264_profile": video_stream.get("profile"),
            "pixel_format": video_stream.get("pix_fmt"),
            "color_space": video_stream.get("color_space"),
            "audio_sample_rate": int(audios[0].get("sample_rate") or 0),
            "video_stream_count": len(videos),
            "narration_engine": request["narration"]["engine"],
            "audio_source": "narration.wav only (input 4:a:0)",
            "video_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        }
    )
    atomic_write_json(metadata_path, render_meta)
    print(
        f"Render verified: {duration:.3f}s, {config['width']}x{config['height']}, "
        f"{actual_fps:.3f} fps, H.264 + one AAC narration stream; "
        "blackdetect=inline_during_render."
    )


if __name__ == "__main__":
    main()
