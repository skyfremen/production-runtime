"""Safe production-equivalent acceptance with no network publication path."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from engine import check as dry_run
from transform import compose as render
from transform import process as production_transform
from transform.synth import OnnxKokoroSynthesizer

PHASE = Path("/tmp/runtime-check-stage")


def phase(name):
    PHASE.write_text(name + "\n", encoding="utf-8")


def write_synthetic_registry():
    assets = []
    for number in (1, 2):
        assets.append({
            "id": f"satisfying-{number:03}", "type": "video", "title": "Synthetic motion",
            "source": "Synthetic", "source_page": "https://example.invalid/source",
            "direct_url": f"https://example.invalid/video-{number}.mp4", "creator": None,
            "license": "Synthetic test data", "commercial_use": True,
            "attribution_required": False, "verified": True,
            "last_verified_at": "2099-01-01T00:00:00Z", "status": "active",
            "orientation": "vertical", "visual_tags": ["motion"],
            "motion_type": "continuous", "motion_intensity": "medium",
            "loopability_score": 90, "visual_satisfaction_score": 90,
            "caption_readability_score": 90, "has_embedded_text": False,
            "has_watermark": False,
            "renditions": [{
                "id": "synthetic", "width": 1080, "height": 1920, "fps": 30,
                "file_type": "video/mp4",
                "direct_url": f"https://example.invalid/video-{number}.mp4",
            }],
        })
    target = Path(__file__).resolve().parent / "media-library/backgrounds.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"schema_version": 3, "assets": assets}) + "\n")


def _ass_seconds(value):
    hours, minutes, seconds = str(value).split(":", 2)
    return (float(hours) * 3600.0) + (float(minutes) * 60.0) + float(seconds)


def _first_active_caption_midpoint(ass_path):
    active_tag = f"{{\\c{production_transform.CAPTION_ACTIVE_ASS}}}"
    for line in Path(ass_path).read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:") or active_tag not in line:
            continue
        parts = line.split(",", 9)
        if len(parts) != 10:
            continue
        start = _ass_seconds(parts[1])
        end = _ass_seconds(parts[2])
        if end > start:
            return (start + end) / 2.0
    raise RuntimeError("Full visual preview contains no timed active-word caption event")


def _count_pixels(image, box, predicate):
    crop = image.crop(box).convert("RGBA")
    return sum(1 for pixel in crop.getdata() if predicate(*pixel))


def _render_full_visual_preview(root, request_path):
    """Render the downloadable preview through the shared production transform path."""
    source = root / "render-smoke"
    target = root / "visual-preview"
    target.mkdir()
    for name in ("background.asset", "background_selection.json"):
        shutil.copy2(source / name, target / name)

    previous_output_dir = render.OUTPUT_DIR
    previous_argv = sys.argv[:]
    previous_caption_events = render.caption_events
    previous_run_capture = render.run_capture
    previous_alignment_metadata = production_transform.LAST_ALIGNMENT_METADATA
    env_keys = (
        "STORY_TEST_MODE",
        "STORY_RENDER_MAX_SECONDS",
        "VIDEO_WIDTH",
        "VIDEO_HEIGHT",
        "VIDEO_FPS",
        "CAPTION_WORD_HIGHLIGHT_ENABLED",
    )
    previous_env = {key: os.environ.get(key) for key in env_keys}

    try:
        render.OUTPUT_DIR = target
        production_transform.LAST_ALIGNMENT_METADATA = {}
        sys.argv = ["process.py", "--request", str(request_path)]
        os.environ.update({
            "STORY_TEST_MODE": "true",
            "STORY_RENDER_MAX_SECONDS": "5",
            "VIDEO_WIDTH": "1080",
            "VIDEO_HEIGHT": "1920",
            "VIDEO_FPS": "30",
            "CAPTION_WORD_HIGHLIGHT_ENABLED": "true",
        })
        production_transform.main()
    finally:
        render.OUTPUT_DIR = previous_output_dir
        render.caption_events = previous_caption_events
        render.run_capture = previous_run_capture
        production_transform.LAST_ALIGNMENT_METADATA = previous_alignment_metadata
        sys.argv = previous_argv
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    output = target / "short.mp4"
    if not output.exists() or output.stat().st_size < 100_000:
        raise RuntimeError("Full visual preview is missing or too small")

    metadata = json.loads((target / "render-metadata.json").read_text(encoding="utf-8"))
    if metadata.get("caption_timing_mode") != "word_aligned":
        raise RuntimeError("Full visual preview did not use real word alignment")
    if metadata.get("caption_word_highlight_applied") is not True:
        raise RuntimeError("Full visual preview did not apply active-word highlighting")
    if float(metadata.get("caption_alignment_coverage") or 0.0) < 0.90:
        raise RuntimeError("Full visual preview alignment coverage is below 0.90")

    active_timestamp = _first_active_caption_midpoint(target / "captions.ass")
    story_start = float(metadata["story_start_seconds"])
    early_timestamp = max(0.15, min(0.75, story_start - 0.20))
    early_frame = target / "frame-full-card.png"
    active_frame = target / "frame-full-caption.png"
    dry_run._extract_frame(output, early_timestamp, early_frame)
    dry_run._extract_frame(output, active_timestamp, active_frame)

    early = Image.open(early_frame).convert("RGBA")
    active = Image.open(active_frame).convert("RGBA")

    card_pixels = _count_pixels(
        early,
        render.CARD_BOX,
        lambda r, g, b, a: a > 180 and r > 215 and g > 215 and b > 215,
    )
    if card_pixels < 10_000:
        raise RuntimeError("Full visual preview opening card is not visibly present")

    handle_pixels = _count_pixels(
        active,
        render.HANDLE_PILL,
        lambda r, g, b, a: a > 180 and r > 205 and g > 205 and b > 205,
    )
    if handle_pixels < 20:
        raise RuntimeError("Full visual preview handle is not visibly present")

    subscribe_pixels = _count_pixels(
        active,
        render.SUBSCRIBE_PILL,
        lambda r, g, b, a: a > 180 and r > 165 and g > 125 and b < 140,
    )
    if subscribe_pixels < 20:
        raise RuntimeError("Full visual preview subscribe treatment is not visibly present")

    highlight_pixels = _count_pixels(
        active,
        (0, 650, render.VIDEO_WIDTH, 1250),
        lambda r, g, b, a: (
            a > 180 and r > 175 and g > 130 and b < 145
            and r > b + 55 and g > b + 35
        ),
    )
    if highlight_pixels < 20:
        raise RuntimeError("Full visual preview contains no visible active-word colour")

    return output, metadata, {
        "opening_card_light_pixels": card_pixels,
        "handle_white_pixels": handle_pixels,
        "subscribe_yellow_pixels": subscribe_pixels,
        "caption_highlight_yellow_pixels": highlight_pixels,
    }


def main():
    phase("s01")
    os.environ["STORY_TEST_MODE"] = "true"
    os.environ["STORY_RENDER_MAX_SECONDS"] = "5"
    write_synthetic_registry()
    with tempfile.TemporaryDirectory(prefix="runtime-acceptance-") as holder:
        root = Path(holder)
        request, stage_counts = dry_run.validate_production_shaped_batch(root)
        dry_run.verify_failure_isolation_fixture(root)
        phase("s02")
        try:
            render_seconds, metrics = dry_run.render_smoke(request, root)
        except Exception as exc:
            if isinstance(exc, subprocess.CalledProcessError):
                command = " ".join(str(x) for x in (exc.cmd or []))
                if "color=c=" in command:
                    phase("s02j")
                elif "caption-contrast.png" in command:
                    phase("s02k")
                elif "transform/verify.py" in command:
                    phase("s02n")
                elif "-frames:v 1" in command:
                    phase("s02m")
                else:
                    phase("s02l")
                raise
            message = str(exc).lower()
            if "identity resource" in message or "font" in message or "emoji" in message:
                phase("s02a")
            elif "ffmpeg" in message or "command" in message or "returned non-zero" in message:
                phase("s02b")
            elif "caption" in message or "card" in message or "pill" in message or "frame" in message:
                phase("s02c")
            elif "metadata" in message or "render-metadata" in message:
                phase("s02d")
            else:
                kind = type(exc).__name__
                mapping = {
                    "AssertionError": "s02e", "FileNotFoundError": "s02f",
                    "KeyError": "s02g", "RuntimeError": "s02h", "SystemExit": "s02i",
                }
                phase(mapping.get(kind, "s02x"))
            raise
        render_meta = json.loads(
            (root / "render-smoke/render-metadata.json").read_text(encoding="utf-8")
        )

        phase("s03")
        synth = OnnxKokoroSynthesizer()
        approved_voices = ("af_heart", "af_bella", "am_echo", "am_fenrir")
        for approved_voice in approved_voices:
            probe_audio, _probe_segments, _probe_metrics = synth.synthesize(
                "The result was clear.", approved_voice, 1.75
            )
            if len(probe_audio) < 2400:
                raise RuntimeError("Approved narration resource validation failed")

        phase("s04")
        visual_preview, preview_meta, visual_metrics = _render_full_visual_preview(root, request)

        result = {
            "items": 24,
            "render_seconds": round(render_seconds, 3),
            "resolution": render_meta["resolution"],
            "fps": render_meta["fps"],
            "video_codec": render_meta["video_codec"],
            "audio_codec": render_meta["audio_codec"],
            "audio_stream_count": render_meta["audio_stream_count"],
            "tts_backend": synth.backend,
            "approved_voice_count": len(approved_voices),
            "tts_init_seconds": synth.init_seconds,
            "alignment_backend": preview_meta["caption_alignment_backend"],
            "alignment_coverage": preview_meta["caption_alignment_coverage"],
            "caption_highlight_smoke_events": preview_meta["caption_word_highlight_event_count"],
            "caption_rapid_highlight_groups": preview_meta["caption_rapid_highlight_group_count"],
            "caption_highlight_yellow_pixels": visual_metrics["caption_highlight_yellow_pixels"],
            "preview_opening_card_pixels": visual_metrics["opening_card_light_pixels"],
            "preview_handle_pixels": visual_metrics["handle_white_pixels"],
            "preview_subscribe_pixels": visual_metrics["subscribe_yellow_pixels"],
            "validation_preview_mode": "full_production_visual_with_word_highlight",
            "validation_count": stage_counts["schema"],
            "upload_count": stage_counts["upload_contract"],
        }
        Path("/tmp/runtime-acceptance-summary.json").write_text(
            json.dumps(result, sort_keys=True) + "\n", encoding="utf-8"
        )

        preview_output = os.environ.get("RUNTIME_PREVIEW_OUTPUT")
        if preview_output:
            preview_target = Path(preview_output)
            preview_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(visual_preview, preview_target)

    phase("s05")
    print("Production-equivalent acceptance PASS: items=24")


if __name__ == "__main__":
    main()
