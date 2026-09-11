"""Safe production-equivalent acceptance with no network publication path."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import soundfile as sf

from engine import check as dry_run
from transform.align import align_story_words
from transform.synth import OnnxKokoroSynthesizer, SAMPLE_RATE

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
        text = "The backup proved it."
        synth = OnnxKokoroSynthesizer()
        audio, segments, _audio_metrics = synth.synthesize(text, "af_heart", 1.75)
        narration = root / "model-check.wav"
        sf.write(narration, audio, SAMPLE_RATE, subtype="PCM_16")
        phase("s04")
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
    phase("s05")
    print("Production-equivalent acceptance PASS: items=24")


if __name__ == "__main__":
    main()
