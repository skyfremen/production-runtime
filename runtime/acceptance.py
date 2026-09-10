"""Safe production-equivalent acceptance with no network publication path."""
import json
import os
import tempfile
from pathlib import Path

import soundfile as sf

from production import dry_run
from rendering.caption_alignment import align_story_words
from rendering.tts_backend import OnnxKokoroSynthesizer, SAMPLE_RATE

PHASE = Path("/tmp/runtime-acceptance-phase")


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
                "id": "synthetic", "width": 720, "height": 1280, "fps": 30,
                "file_type": "video/mp4",
                "direct_url": f"https://example.invalid/video-{number}.mp4",
            }],
        })
    target = Path(__file__).resolve().parent / "media-library/backgrounds.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"schema_version": 3, "assets": assets}) + "\n")


def main():
    phase("batch")
    os.environ["STORY_TEST_MODE"] = "true"
    os.environ["STORY_RENDER_MAX_SECONDS"] = "5"
    write_synthetic_registry()
    with tempfile.TemporaryDirectory(prefix="runtime-acceptance-") as holder:
        root = Path(holder)
        request, stage_counts = dry_run.validate_production_shaped_batch(root)
        dry_run.verify_failure_isolation_fixture(root)
        phase("render")
        try:
            render_seconds, metrics = dry_run.render_smoke(request, root)
        except Exception as exc:
            message = str(exc).lower()
            if "channel-avatar" in message or "font" in message or "emoji" in message:
                phase("render_asset")
            elif "ffmpeg" in message or "command" in message or "returned non-zero" in message:
                phase("render_encode")
            elif "caption" in message or "card" in message or "pill" in message or "frame" in message:
                phase("render_layout")
            elif "metadata" in message or "render-metadata" in message:
                phase("render_metadata")
            else:
                kind = type(exc).__name__
                phase("render_" + (kind if kind in {"AssertionError", "FileNotFoundError", "KeyError", "RuntimeError", "SystemExit"} else "other"))
            raise
        render_meta = json.loads(
            (root / "render-smoke/render-metadata.json").read_text(encoding="utf-8")
        )

        phase("tts")
        text = "The backup proved it."
        synth = OnnxKokoroSynthesizer()
        audio, segments, _audio_metrics = synth.synthesize(text, "af_heart", 1.75)
        narration = root / "model-check.wav"
        sf.write(narration, audio, SAMPLE_RATE, subtype="PCM_16")
        phase("alignment")
        words, alignment = align_story_words(
            narration, text, segments, 0.0, len(audio) / SAMPLE_RATE
        )
        if not words or alignment["caption_alignment_coverage"] < 0.9:
            raise RuntimeError("Model acceptance did not meet alignment coverage")

        result = {
            "items": 24,
            "render_seconds": round(render_seconds, 3),
            "resolution": render_meta["resolution"],
            "fps": render_meta["fps"],
            "video_codec": render_meta["video_codec"],
            "audio_codec": render_meta["audio_codec"],
            "audio_stream_count": render_meta["audio_stream_count"],
            "tts_backend": synth.backend,
            "tts_voice": "af_heart",
            "tts_speed": 1.75,
            "tts_init_seconds": synth.init_seconds,
            "alignment_backend": alignment["caption_alignment_backend"],
            "alignment_coverage": alignment["caption_alignment_coverage"],
            "validation_count": stage_counts["schema"],
            "upload_count": stage_counts["upload_contract"],
        }
        Path("/tmp/runtime-acceptance-summary.json").write_text(
            json.dumps(result, sort_keys=True) + "\n", encoding="utf-8"
        )
    phase("complete")
    print("Production-equivalent acceptance PASS: items=24")


if __name__ == "__main__":
    main()
