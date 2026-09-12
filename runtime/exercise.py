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
PREVIEW_SECONDS = "15"


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


def _first_caption_midpoint(ass_path, marker, label):
    for line in Path(ass_path).read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:") or marker not in line:
            continue
        parts = line.split(",", 9)
        if len(parts) != 10:
            continue
        start = _ass_seconds(parts[1])
        end = _ass_seconds(parts[2])
        if end > start:
            return (start + end) / 2.0
    raise RuntimeError(f"Full visual preview contains no timed {label} caption event")


def _count_pixels(image, box, predicate):
    crop = image.crop(box).convert("RGBA")
    return sum(1 for pixel in crop.getdata() if predicate(*pixel))


def _caption_bounds(image):
    pixels = image.convert("RGB").load()
    points = []
    for y in range(650, 1250):
        for x in range(render.VIDEO_WIDTH):
            r, g, b = pixels[x, y]
            white = r > 205 and g > 205 and b > 205
            accent = r > 200 and g > 105 and b < 150 and r > b + 60
            if white or accent:
                points.append((x, y))
    if not points:
        raise RuntimeError("Full visual preview contains no visible caption pixels")
    return min(x for x, _ in points), max(x for x, _ in points)


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
    previous_punchline = production_transform.CURRENT_PUNCHLINE
    env_keys = (
        "STORY_TEST_MODE",
        "STORY_RENDER_MAX_SECONDS",
        "VIDEO_WIDTH",
        "VIDEO_HEIGHT",
        "VIDEO_FPS",
        "CAPTION_WORD_HIGHLIGHT_ENABLED",
        "CAPTION_SEMANTIC_EMPHASIS_ENABLED",
    )
    previous_env = {key: os.environ.get(key) for key in env_keys}

    try:
        render.OUTPUT_DIR = target
        production_transform.LAST_ALIGNMENT_METADATA = {}
        production_transform.CURRENT_PUNCHLINE = None
        sys.argv = ["process.py", "--request", str(request_path)]
        os.environ.update({
            "STORY_TEST_MODE": "true",
            "STORY_RENDER_MAX_SECONDS": PREVIEW_SECONDS,
            "VIDEO_WIDTH": "1080",
            "VIDEO_HEIGHT": "1920",
            "VIDEO_FPS": "30",
            "CAPTION_WORD_HIGHLIGHT_ENABLED": "true",
            "CAPTION_SEMANTIC_EMPHASIS_ENABLED": "true",
        })
        production_transform.main()
    finally:
        render.OUTPUT_DIR = previous_output_dir
        render.caption_events = previous_caption_events
        render.run_capture = previous_run_capture
        production_transform.LAST_ALIGNMENT_METADATA = previous_alignment_metadata
        production_transform.CURRENT_PUNCHLINE = previous_punchline
        sys.argv = previous_argv
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    output = target / "short.mp4"
    if not output.exists() or output.stat().st_size < 100_000:
        raise RuntimeError("Full visual preview is missing or too small")
    narration = target / "narration.wav"
    if not narration.exists() or narration.stat().st_size < 8_000:
        raise RuntimeError("Full visual preview narration audio is missing or too small")

    verify_env = os.environ.copy()
    verify_env.update({
        "PYTHONPATH": str(Path(__file__).resolve().parent),
        "STORY_OUTPUT_DIR": str(target),
        "STORY_TEST_MODE": "true",
        "STORY_RENDER_MAX_SECONDS": PREVIEW_SECONDS,
        "VIDEO_WIDTH": "1080",
        "VIDEO_HEIGHT": "1920",
        "VIDEO_FPS": "30",
    })
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "transform" / "verify.py"), "--request", str(request_path)],
        cwd=Path(__file__).resolve().parent.parent,
        env=verify_env,
        check=True,
    )

    metadata = json.loads((target / "render-metadata.json").read_text(encoding="utf-8"))
    if metadata.get("render_verified") is not True:
        raise RuntimeError("Full visual preview failed canonical render verification")
    if metadata.get("caption_timing_mode") != "word_aligned":
        raise RuntimeError("Full visual preview did not use real word alignment")
    if metadata.get("caption_word_highlight_applied") is not True:
        raise RuntimeError("Full visual preview did not apply active-word highlighting")
    if metadata.get("caption_semantic_emphasis_enabled") is not True:
        raise RuntimeError("Full visual preview semantic emphasis feature is not enabled")
    if metadata.get("caption_semantic_emphasis_applied") is not True:
        raise RuntimeError("Full visual preview did not apply semantic punchline emphasis")
    if metadata.get("caption_punchline_match_status") != "matched":
        raise RuntimeError("Full visual preview did not deterministically match its semantic punchline")
    if int(metadata.get("caption_punchline_word_count") or 0) < 2:
        raise RuntimeError("Full visual preview matched an implausibly short punchline span")
    if int(metadata.get("caption_emphasis_word_count") or 0) < 1:
        raise RuntimeError("Full visual preview matched no emphasis words")
    if float(metadata.get("caption_alignment_coverage") or 0.0) < 0.90:
        raise RuntimeError("Full visual preview alignment coverage is below 0.90")

    ass_path = target / "captions.ass"
    active_timestamp = _first_caption_midpoint(
        ass_path, production_transform.CAPTION_ACTIVE_ASS, "active-word"
    )
    semantic_timestamp = _first_caption_midpoint(
        ass_path, production_transform.CAPTION_EMPHASIS_ASS, "semantic-emphasis"
    )
    story_start = float(metadata["story_start_seconds"])
    if semantic_timestamp <= story_start:
        raise RuntimeError("Semantic emphasis began before story narration")
    early_timestamp = max(0.15, min(0.75, story_start - 0.20))
    early_frame = target / "frame-full-card.png"
    active_frame = target / "frame-full-caption.png"
    semantic_frame = target / "frame-full-semantic.png"
    dry_run._extract_frame(output, early_timestamp, early_frame)
    dry_run._extract_frame(output, active_timestamp, active_frame)
    dry_run._extract_frame(output, semantic_timestamp, semantic_frame)

    early = Image.open(early_frame).convert("RGBA")
    active = Image.open(active_frame).convert("RGBA")
    semantic = Image.open(semantic_frame).convert("RGBA")

    card_pixels = _count_pixels(
        early,
        render.CARD_BOX,
        lambda r, g, b, a: a > 180 and r > 215 and g > 215 and b > 215,
    )
    if card_pixels < 10_000:
        raise RuntimeError("Full visual preview opening card is not visibly present")
    late_card_pixels = _count_pixels(
        semantic,
        render.CARD_BOX,
        lambda r, g, b, a: a > 180 and r > 215 and g > 215 and b > 215,
    )
    if late_card_pixels > max(2_000, card_pixels * 0.25):
        raise RuntimeError("Full visual preview opening card did not disappear before the punchline")

    handle_pixels = _count_pixels(
        semantic,
        render.HANDLE_PILL,
        lambda r, g, b, a: a > 180 and r > 205 and g > 205 and b > 205,
    )
    if handle_pixels < 20:
        raise RuntimeError("Full visual preview handle is not visibly present")

    subscribe_pixels = _count_pixels(
        semantic,
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

    semantic_pixels = _count_pixels(
        semantic,
        (0, 650, render.VIDEO_WIDTH, 1250),
        lambda r, g, b, a: a > 180 and r > 220 and 105 < g < 205 and b < 100,
    )
    if semantic_pixels < 20:
        raise RuntimeError("Full visual preview contains no visibly distinct semantic emphasis colour")

    min_x, max_x = _caption_bounds(semantic)
    safe_slack = render.CAPTION_OUTLINE + 4
    if min_x < render.CAPTION_MARGIN_X - safe_slack:
        raise RuntimeError(f"Semantic caption crossed the left safe margin: x={min_x}")
    if max_x > render.VIDEO_WIDTH - render.CAPTION_MARGIN_X + safe_slack:
        raise RuntimeError(f"Semantic caption crossed the right safe margin: x={max_x}")

    return output, metadata, {
        "opening_card_light_pixels": card_pixels,
        "opening_card_late_light_pixels": late_card_pixels,
        "handle_white_pixels": handle_pixels,
        "subscribe_yellow_pixels": subscribe_pixels,
        "caption_highlight_yellow_pixels": highlight_pixels,
        "caption_semantic_orange_pixels": semantic_pixels,
        "caption_leftmost_x": min_x,
        "caption_rightmost_x": max_x,
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
            "caption_semantic_emphasis_applied": preview_meta["caption_semantic_emphasis_applied"],
            "caption_punchline_match_status": preview_meta["caption_punchline_match_status"],
            "caption_punchline_word_count": preview_meta["caption_punchline_word_count"],
            "caption_emphasis_word_count": preview_meta["caption_emphasis_word_count"],
            "caption_semantic_orange_pixels": visual_metrics["caption_semantic_orange_pixels"],
            "preview_opening_card_pixels": visual_metrics["opening_card_light_pixels"],
            "preview_opening_card_late_pixels": visual_metrics["opening_card_late_light_pixels"],
            "preview_handle_pixels": visual_metrics["handle_white_pixels"],
            "preview_subscribe_pixels": visual_metrics["subscribe_yellow_pixels"],
            "preview_caption_leftmost_x": visual_metrics["caption_leftmost_x"],
            "preview_caption_rightmost_x": visual_metrics["caption_rightmost_x"],
            "validation_preview_mode": "full_production_visual_with_word_and_semantic_emphasis",
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
