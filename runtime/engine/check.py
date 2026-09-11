import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageStat

from resources.validate import load_registry, validate_request_backgrounds
from profile.config import EDITORIAL_WEIGHTS, TITLE_WEIGHTS
from engine.batch import order_requests, validate_explicit_batch, write_batch_files
from output.execute import scheduled_slot_guard
from output.transfer import build_upload_body
from transform import compose as render
from guard.schema import validate_request_data


BASE = Path(__file__).resolve().parents[1]
REPO_ROOT = BASE.parent
DAILY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "daily-production.yml"
DRY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dry-run.yml"

FIXTURE_DATE = "2099-09-10"
FIXTURE_NOW = datetime(2099, 9, 9, 0, 0, tzinfo=timezone.utc)
FIXTURE_START_UTC = datetime(2099, 9, 9, 16, 0, tzinfo=timezone.utc)


def _title_candidate(title, style, score):
    return {
        "title": title,
        "style": style,
        "truthful": True,
        "score": score,
        "score_components": {key: score for key in TITLE_WEIGHTS},
    }


def synthetic_request(index):
    hour = index
    content_id = f"wd-20990910T{hour:02}0000-dry-run-d{index:05d}"
    title = f"Story {index + 1:02}: The Backup Exposed What Really Happened #Shorts"
    publish_at = (FIXTURE_START_UTC + timedelta(hours=index)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema_version": 3,
        "content_id": content_id,
        "channel": {"name": "Wa" + "cky" + " Dramas", "handle": "@WA" + "CKY" + "DRAMAS"},
        "story": {
            "category": "WORKPLACE",
            "story_type": "BACKFIRE",
            "hook": "The Backup He Forgot About",
            "script": (
                f"My boss told everyone story {index + 1} had no proof. "
                "I opened the archived workspace and found the timestamped backup."
            ),
            "card_emojis": ["💼", "🗂️", "😳", "💾", "🔥"],
        },
        "narration": {"engine": "kokoro", "voice": "af_heart", "speed": 1.75},
        "visual": {
            "background_primary_id": "satisfying-001",
            "background_backup_id": "satisfying-002",
        },
        "youtube": {
            "title": title,
            "description": (
                "The archived timestamp changed the whole argument. "
                "Would you have confronted the person who denied it?"
            ),
            "hashtags": [
                "#Shorts",
                "#Wa" + "cky" + "Dramas",
                "#WorkplaceDrama",
                "#Storytime",
            ],
            "tags": [
                "wa" + "cky" + " dramas",
                "workplace drama",
                "boss story",
                "office conflict",
                "evidence backfire",
                "storytime",
            ],
            "category_id": "24",
            "made_for_kids": False,
        },
        "publication": {
            "mode": "scheduled",
            "timezone": "Asia/Singapore",
            "publish_at": publish_at,
        },
        "planning": {
            "plan_date": FIXTURE_DATE,
            "editorial_score": 88.0,
            "editorial_components": {key: 88 for key in EDITORIAL_WEIGHTS},
            "analytics_score": None,
            "analytics_weight": 0.0,
            "final_score": 87.5,
            "title_candidates": [
                _title_candidate(title, "HIDDEN_REVELATION", 91),
                _title_candidate(
                    f"Story {index + 1:02}: I Checked the Archive and Found the Proof #Shorts",
                    "DISCOVERY",
                    86,
                ),
                _title_candidate(
                    f"Story {index + 1:02}: It Looked Normal Until the Timestamp Appeared #Shorts",
                    "NORMAL_TO_ABNORMAL",
                    84,
                ),
                _title_candidate(
                    f"Story {index + 1:02}: I Kept the Archive and the Story Changed #Shorts",
                    "DECISION_CONSEQUENCE",
                    83,
                ),
                _title_candidate(
                    f"Story {index + 1:02}: Hours Before the Audit, I Found the Backup #Shorts",
                    "COUNTDOWN",
                    82,
                ),
            ],
            "selected_title_score": 91.0,
            "hook_score": 90.0,
            "selection_class": "exploit" if index < 19 else "explore",
            "selection_reason": (
                "Strong contradiction, proof-driven escalation and clear reversal."
            ),
            "similarity": {"max_recent_similarity": 0.21},
            "attributes": {
                "subtype": "EVIDENCE_BACKFIRE",
                "conflict": "HIDDEN_FILE",
                "primary_emotion": "INJUSTICE",
                "protagonist_role": "EMPLOYEE",
                "antagonist_role": "BOSS",
                "opening_style": "CONTRADICTION",
                "title_style": "HIDDEN_REVELATION",
                "ending_style": "REVERSAL",
            },
            "target_duration_seconds": 151,
        },
    }


def build_synthetic_batch(root):
    requests_dir = root / "content" / "requests"
    planning_dir = root / "content" / "planning"
    requests_dir.mkdir(parents=True)
    planning_dir.mkdir(parents=True)

    requests = []
    content_ids = []
    payloads = []
    for index in range(24):
        payload = synthetic_request(index)
        path = requests_dir / f"{payload['content_id']}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        requests.append(str(path))
        content_ids.append(payload["content_id"])
        payloads.append(payload)

    planning_path = planning_dir / f"{FIXTURE_DATE}.json"
    planning_path.write_text(
        json.dumps(
            {
                "plan_date": FIXTURE_DATE,
                "final_selected": 24,
                "content_ids": content_ids,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return requests, str(planning_path), payloads


def validate_production_shaped_batch(root):
    requests, planning_path, payloads = build_synthetic_batch(root)
    batch = validate_explicit_batch(requests, [planning_path])
    ordered = order_requests(batch.requests)
    if len(ordered) != 24 or len(set(ordered)) != 24:
        raise AssertionError("Synthetic production batch did not resolve to exactly 24 unique requests")

    request_output = root / "batch-requests.txt"
    sourcing_output = root / "background-sourcing-manifests.txt"
    written = write_batch_files(
        batch,
        request_output=request_output,
        sourcing_output=sourcing_output,
    )
    if tuple(ordered) != tuple(written):
        raise AssertionError("Shared batch writer changed deterministic request ordering")
    if request_output.read_text(encoding="utf-8").splitlines() != list(ordered):
        raise AssertionError("Shared batch request output differs from resolved order")
    if sourcing_output.read_text(encoding="utf-8"):
        raise AssertionError("Synthetic fixture unexpectedly requires external background sourcing")

    registry = load_registry()
    stage_counts = {
        "schema": 0,
        "background": 0,
        "schedule_guard": 0,
        "upload_contract": 0,
        "upload_boundary_simulated": 0,
        "publication_boundary_simulated": 0,
        "receipt_boundary_simulated": 0,
        "analytics_boundary_simulated": 0,
    }

    payload_by_path = {path: payload for path, payload in zip(requests, payloads)}
    simulated_receipts = []
    for path in ordered:
        payload = payload_by_path[path]
        errors = validate_request_data(payload, request_path=path)
        if errors:
            raise AssertionError(f"Production request validation failed for {payload['content_id']}: {errors}")
        stage_counts["schema"] += 1

        validate_request_backgrounds(payload, registry)
        stage_counts["background"] += 1

        guard = scheduled_slot_guard(payload, now_utc=FIXTURE_NOW)
        if guard["skip"]:
            raise AssertionError(f"Synthetic future publication slot was incorrectly skipped: {guard}")
        stage_counts["schedule_guard"] += 1

        body = build_upload_body(payload, now_utc=FIXTURE_NOW)
        if body["status"]["privacyStatus"] != "private":
            raise AssertionError("Scheduled upload contract is no longer private-before-publish")
        if body["status"]["publishAt"] != payload["publication"]["publish_at"]:
            raise AssertionError("Scheduled upload contract changed immutable publish_at")
        stage_counts["upload_contract"] += 1

        fake_video_id = f"dry{payload['content_id'][-8:]}"[-11:]
        simulated_upload = {
            "content_id": payload["content_id"],
            "youtube_video_id": fake_video_id,
            "simulated": True,
        }
        stage_counts["upload_boundary_simulated"] += 1

        simulated_publication = {
            **simulated_upload,
            "publication_verified": True,
            "publish_at": payload["publication"]["publish_at"],
        }
        stage_counts["publication_boundary_simulated"] += 1

        simulated_receipts.append(
            {
                "schema_version": 3,
                **simulated_publication,
                "receipt_simulated": True,
            }
        )
        stage_counts["receipt_boundary_simulated"] += 1
        stage_counts["analytics_boundary_simulated"] += 1

    if any(value != 24 for value in stage_counts.values()):
        raise AssertionError(f"Not all 24 requests traversed the safe production sequence: {stage_counts}")
    if len(simulated_receipts) != 24:
        raise AssertionError("Boundary simulation did not produce one result per request")

    return ordered[0], stage_counts


def verify_failure_isolation_fixture(root):
    good = [synthetic_request(index) for index in range(24)]
    bad = copy.deepcopy(good[12])
    bad.pop("youtube")
    bad_path = root / f"{bad['content_id']}.json"
    bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    bad_errors = validate_request_data(bad, request_path=bad_path)
    if not bad_errors:
        raise AssertionError("Malformed per-video request was not rejected")
    for index, payload in enumerate(good):
        if index == 12:
            continue
        path = root / f"{payload['content_id']}.json"
        if validate_request_data(payload, request_path=path):
            raise AssertionError("A malformed synthetic neighbor contaminated an unrelated valid request")


class _FakeSynthesizer:
    def __init__(self):
        self.init_seconds = 0.0
        self._calls = 0

    def synthesize(self, text, voice, speed):
        self._calls += 1
        duration = 1.2 if self._calls == 1 else 1.6
        samples = int(round(24000 * duration))
        t = np.arange(samples, dtype=np.float32) / 24000.0
        audio = (0.08 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
        return audio, [(text, samples)], {"dry_run_fake_tts": True}


def _write_wave(path, audio, samplerate):
    clipped = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(int(samplerate))
        out.writeframes(pcm.tobytes())


def _make_background(path):
    render.run_capture(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x355070:s=720x1280:r=30:d=4.2",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            render.X264_PRESET,
            "-crf",
            str(render.X264_CRF),
            "-pix_fmt",
            "yuv420p",
            "-f",
            "mp4",
            str(path),
        ]
    )


def _extract_frame(video, timestamp, output):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-ss",
            f"{timestamp:.2f}",
            "-frames:v",
            "1",
            str(output),
        ],
        check=True,
    )


def _count_pixels(image, box, predicate):
    crop = image.crop(box).convert("RGBA")
    return sum(1 for pixel in crop.getdata() if predicate(*pixel))


def _validate_template_contract(smoke_dir):
    card = Image.open(smoke_dir / "story-card.png").convert("RGBA")
    brand = Image.open(smoke_dir / "branding.png").convert("RGBA")
    early = Image.open(smoke_dir / "frame-card-visible.png").convert("RGB")
    story = Image.open(smoke_dir / "frame-story.png").convert("RGB")

    expected_size = (render.VIDEO_WIDTH, render.VIDEO_HEIGHT)
    for label, image in (("card", card), ("brand", brand), ("early frame", early), ("story frame", story)):
        if image.size != expected_size:
            raise AssertionError(f"{label} changed production dimensions: {image.size}")

    if render.CAPTION_MARGIN_X != 85:
        raise AssertionError(f"Caption safe margin changed from 85px: {render.CAPTION_MARGIN_X}")
    if render.CAPTION_MAX_WIDTH != render.VIDEO_WIDTH - 2 * render.CAPTION_MARGIN_X:
        raise AssertionError("Caption maximum width no longer derives symmetrically from the safe margins")
    if render.CARD_BOX != (37, 157, 683, 537):
        raise AssertionError(f"Opening card bounds changed unexpectedly: {render.CARD_BOX}")
    if render.HANDLE_PILL != (190, 840, 530, 889):
        raise AssertionError(f"Handle pill bounds changed unexpectedly: {render.HANDLE_PILL}")
    if render.SUBSCRIBE_PILL != (240, 899, 480, 945):
        raise AssertionError(f"Subscribe pill bounds changed unexpectedly: {render.SUBSCRIBE_PILL}")

    card_alpha = ImageStat.Stat(card.crop(render.CARD_BOX).getchannel("A")).mean[0]
    if card_alpha < 240:
        raise AssertionError(f"Opening card is not opaque enough in its canonical region: {card_alpha:.1f}")

    white_handle_pixels = _count_pixels(
        brand,
        render.HANDLE_PILL,
        lambda r, g, b, a: a > 180 and r > 210 and g > 210 and b > 210,
    )
    if white_handle_pixels < 40:
        raise AssertionError("Handle pill no longer contains clearly visible white handle text")

    yellow_subscribe_pixels = _count_pixels(
        brand,
        render.SUBSCRIBE_PILL,
        lambda r, g, b, a: a > 180 and r > 180 and g > 140 and b < 120,
    )
    if yellow_subscribe_pixels < 40:
        raise AssertionError("Subscribe pill no longer contains clearly visible yellow SUBSCRIBE text")

    early_card_mean = sum(ImageStat.Stat(early.crop(render.CARD_BOX)).mean) / 3.0
    story_card_mean = sum(ImageStat.Stat(story.crop(render.CARD_BOX)).mean) / 3.0
    if early_card_mean - story_card_mean < 55:
        raise AssertionError(
            "Opening card did not visibly disappear after its production fade window "
            f"(early={early_card_mean:.1f}, story={story_card_mean:.1f})"
        )

    story_y1, story_y2 = 500, 780
    bright = []
    pixels = story.load()
    for y in range(story_y1, story_y2):
        for x in range(render.VIDEO_WIDTH):
            r, g, b = pixels[x, y]
            if r > 215 and g > 215 and b > 215:
                bright.append((x, y))
    if not bright:
        raise AssertionError("Story frame contains no visible white caption pixels")
    min_x = min(x for x, _y in bright)
    max_x = max(x for x, _y in bright)
    safe_slack = render.CAPTION_OUTLINE + 2
    if min_x < render.CAPTION_MARGIN_X - safe_slack:
        raise AssertionError(f"Caption crossed the left safe zone: x={min_x}")
    if max_x > render.VIDEO_WIDTH - render.CAPTION_MARGIN_X + safe_slack:
        raise AssertionError(f"Caption crossed the right safe zone: x={max_x}")

    late_subscribe_pixels = _count_pixels(
        story.convert("RGBA"),
        render.SUBSCRIBE_PILL,
        lambda r, g, b, a: r > 165 and g > 125 and b < 130,
    )
    if late_subscribe_pixels < 20:
        raise AssertionError("Branding/Subscribe pill did not persist after the opening card disappeared")

    meta = json.loads((smoke_dir / "render-metadata.json").read_text(encoding="utf-8"))
    if abs(float(meta["card_transition_seconds"]) - render.CARD_TRANSITION_SECONDS) > 0.001:
        raise AssertionError("Rendered card transition duration diverged from the production constant")
    if not 1.95 <= float(meta["story_start_seconds"]) <= 2.05:
        raise AssertionError(
            "Deterministic template fixture no longer reaches story/caption state at about 2 seconds: "
            f"{meta['story_start_seconds']}"
        )

    return {
        "caption_leftmost_x": min_x,
        "caption_rightmost_x": max_x,
        "card_visible_mean": round(early_card_mean, 2),
        "card_gone_mean": round(story_card_mean, 2),
        "handle_white_pixels": white_handle_pixels,
        "subscribe_yellow_pixels": yellow_subscribe_pixels,
    }


def render_smoke(request_path, root):
    """Exercise the actual production renderer with fake TTS and local media boundaries."""
    smoke_dir = root / "render-smoke"
    smoke_dir.mkdir()
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))

    selection = {
        "content_id": request["content_id"],
        "selected_background_id": request["visual"]["background_primary_id"],
        "metrics": {"resolution_started_at": datetime.now(timezone.utc).isoformat()},
        "dry_run_fixture": True,
    }
    (smoke_dir / "background_selection.json").write_text(
        json.dumps(selection, indent=2) + "\n",
        encoding="utf-8",
    )
    _make_background(smoke_dir / "background.asset")

    previous_output_dir = render.OUTPUT_DIR
    previous_onnx = render.OnnxKokoroSynthesizer
    previous_pytorch = render.PytorchKokoroSynthesizer
    previous_argv = sys.argv[:]
    previous_soundfile = sys.modules.get("soundfile")
    env_keys = ("STORY_TEST_MODE", "STORY_RENDER_MAX_SECONDS", "VIDEO_WIDTH", "VIDEO_HEIGHT", "VIDEO_FPS")
    previous_env = {key: os.environ.get(key) for key in env_keys}

    fake_soundfile = types.ModuleType("soundfile")
    fake_soundfile.write = _write_wave

    try:
        render.OUTPUT_DIR = smoke_dir
        render.OnnxKokoroSynthesizer = _FakeSynthesizer
        render.PytorchKokoroSynthesizer = _FakeSynthesizer
        sys.modules["soundfile"] = fake_soundfile
        sys.argv = ["render.py", "--request", str(request_path)]
        os.environ.update(
            {
                "STORY_TEST_MODE": "true",
                "STORY_RENDER_MAX_SECONDS": "5",
                "VIDEO_WIDTH": "720",
                "VIDEO_HEIGHT": "1280",
                "VIDEO_FPS": "30",
            }
        )
        render.main()
    finally:
        render.OUTPUT_DIR = previous_output_dir
        render.OnnxKokoroSynthesizer = previous_onnx
        render.PytorchKokoroSynthesizer = previous_pytorch
        sys.argv = previous_argv
        if previous_soundfile is None:
            sys.modules.pop("soundfile", None)
        else:
            sys.modules["soundfile"] = previous_soundfile
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    output = smoke_dir / "short.mp4"
    if not output.exists() or output.stat().st_size < 100000:
        raise AssertionError("Production template smoke output is missing or too small")

    _extract_frame(output, 1.00, smoke_dir / "frame-card-visible.png")
    _extract_frame(output, 2.30, smoke_dir / "frame-story.png")
    template_metrics = _validate_template_contract(smoke_dir)

    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(BASE),
            "STORY_OUTPUT_DIR": str(smoke_dir),
            "STORY_TEST_MODE": "true",
            "STORY_RENDER_MAX_SECONDS": "5",
            "VIDEO_WIDTH": "720",
            "VIDEO_HEIGHT": "1280",
            "VIDEO_FPS": "30",
        }
    )
    subprocess.run(
        [
            os.sys.executable,
            str(BASE / "rendering" / "verify_render.py"),
            "--request",
            request_path,
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )
    verified = json.loads((smoke_dir / "render-metadata.json").read_text(encoding="utf-8"))
    if verified.get("render_verified") is not True:
        raise AssertionError("Canonical render verifier did not approve the production template fixture")

    return float(verified.get("ffmpeg_duration_seconds") or 0.0), template_metrics


def verify_workflow_drift_contract():
    daily = DAILY_WORKFLOW.read_text(encoding="utf-8")
    dry = DRY_WORKFLOW.read_text(encoding="utf-8")
    shared = "python runtime/engine/batch.py"
    if shared not in daily:
        raise AssertionError("Daily Production no longer invokes shared production batch orchestration")
    if "python runtime/engine/check.py" not in dry:
        raise AssertionError("Dry Run no longer executes the production-equivalence harness")

    forbidden_dry = (
        "output/execute.py --stage upload",
        "output/verify.py --request",
        "output/receipt.py --request",
        "resources/registry.py ingest-manifest",
        "git push origin",
    )
    found = [token for token in forbidden_dry if token in dry]
    if found:
        raise AssertionError(f"Dry Run contains forbidden production side-effect path(s): {found}")


def main():
    verify_workflow_drift_contract()
    with tempfile.TemporaryDirectory(prefix="runtime-dramas-dry-run-") as temp:
        root = Path(temp)
        first_request, stage_counts = validate_production_shaped_batch(root)
        verify_failure_isolation_fixture(root)
        render_seconds, template_metrics = render_smoke(first_request, root)

    print(
        "Exercise PASS: "
        "24 representative inputs used shared orchestration and explicit side-effect boundaries; "
        "one deterministic "
        "fixture then executed the actual transformation with synthetic local inputs, "
        f"passed canonical verification and template contracts ({render_seconds:.3f}s)."
    )
    print("Template metrics:", json.dumps(template_metrics, sort_keys=True))
    print("Stage counts:", json.dumps(stage_counts, sort_keys=True))


if __name__ == "__main__":
    main()
