"""Offline structural and deterministic self-test for Wacky Dramas V1."""
import json
from pathlib import Path

from base.compat import CONTRACT, contract_hash, validate_contract_hash
from guard.schema import validate_request_data
from output.state import evidence_path, result_path
from output.transfer import build_upload_body, prepare_upload
from transform.semantic import resolve_semantic_span

ROOT = Path(__file__).resolve().parents[1]
checks = 0


def ok(condition, name):
    global checks
    if not condition:
        raise AssertionError(name)
    checks += 1
    print("PASS", name)


def sample_request():
    script_words = ["word"] * 360
    payoff = "the receipt proved everything"
    script_words[200:205] = payoff.split()
    return {
        "request_version": 1,
        "content_id": "wd-" + "a" * 24,
        "source_draft_id": "draft-selftest01",
        "channel": {"name": "Wacky Dramas", "handle": "@WACKYDRAMAS"},
        "story": {
            "category": "work",
            "premise": "A coworker steals credit.",
            "conflict": "The liar gets praised.",
            "twist": "A timestamped receipt exists.",
            "hook": "Everyone believed the wrong person.",
            "script": " ".join(script_words),
            "lead_gender": "female",
            "story_tone": "dramatic",
            "punchline": payoff,
            "card_emojis": ["😳", "💬", "🔥", "👀"],
        },
        "narration": {"engine": "kokoro", "voice": "af_bella", "speed": 1.75},
        "background": {
            "mode": "concatenated_fit_to_short",
            "segments": [
                {"background_id": "a", "segment_start_seconds": 0.0, "segment_duration_seconds": 60.0},
                {"background_id": "b", "segment_start_seconds": 0.0, "segment_duration_seconds": 60.0},
                {"background_id": "c", "segment_start_seconds": 0.0, "segment_duration_seconds": 60.0},
            ],
        },
        "youtube": {
            "title": "The Receipt Changed Everything",
            "description": "A workplace story.",
            "hashtags": ["#WackyDramas", "#Shorts"],
            "tags": ["Wacky Dramas", "Shorts"],
            "category_id": "24",
            "made_for_kids": False,
        },
        "visibility": "public",
        "render": {
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "video_codec": "h264",
            "h264_profile": "high",
            "pixel_format": "yuv420p",
            "audio_codec": "aac",
            "audio_sample_rate": 48000,
            "background_music": False,
        },
    }


class EmptyState:
    def __init__(self):
        self.created = []

    def load(self, _path):
        return None

    def create(self, path, data):
        self.created.append((path, data))
        raise AssertionError("prepare_upload must not create the irreversible intent")


class Channels:
    def list(self, **_kwargs):
        return self

    def execute(self):
        return {"items": [{"id": "UCvrq2m9G4yrwPfL_X-QPzMA", "contentDetails": {"relatedPlaylists": {"uploads": "PL"}}}]}


class FakeYoutube:
    def channels(self):
        return Channels()


def main():
    request = sample_request()
    validate_request_data(request)
    ok(True, "v1 request schema")

    missing_punchline = json.loads(json.dumps(request))
    missing_punchline["story"].pop("punchline")
    validate_request_data(missing_punchline)
    ok(True, "missing punchline is non-blocking")

    null_punchline = json.loads(json.dumps(request))
    null_punchline["story"]["punchline"] = None
    validate_request_data(null_punchline)
    ok(True, "null punchline is non-blocking")

    empty_punchline = json.loads(json.dumps(request))
    empty_punchline["story"]["punchline"] = ""
    validate_request_data(empty_punchline)
    ok(True, "empty punchline is non-blocking")

    unmatched_punchline = json.loads(json.dumps(request))
    unmatched_punchline["story"]["punchline"] = "this phrase is absent"
    validate_request_data(unmatched_punchline)
    ok(True, "unmatched punchline is non-blocking")

    semantic_words = [
        {"word": word, "start": index * 0.1, "end": (index + 1) * 0.1}
        for index, word in enumerate("before the receipt proved everything after".split())
    ]
    semantic = resolve_semantic_span(semantic_words, "the receipt proved everything")
    ok(
        semantic["status"] == "matched"
        and semantic["punchline_indices"] == frozenset({1, 2, 3, 4})
        and semantic["emphasis_indices"] == frozenset(),
        "v1 string punchline resolves for orange highlighting",
    )
    missing_semantic = resolve_semantic_span(semantic_words, None)
    ok(
        missing_semantic["status"] == "missing_metadata"
        and not missing_semantic["punchline_indices"],
        "missing punchline falls back to ordinary highlighting",
    )
    empty_semantic = resolve_semantic_span(semantic_words, "")
    ok(
        empty_semantic["status"] == "missing_metadata"
        and not empty_semantic["punchline_indices"],
        "empty punchline falls back to ordinary highlighting",
    )
    unmatched_semantic = resolve_semantic_span(semantic_words, "this phrase is absent")
    ok(
        unmatched_semantic["status"] == "punchline_not_found"
        and not unmatched_semantic["punchline_indices"],
        "unmatched punchline falls back to ordinary highlighting",
    )
    legacy_semantic = resolve_semantic_span(
        semantic_words,
        {"text": "the receipt proved everything", "emphasis_text": "proved everything"},
    )
    ok(legacy_semantic["status"] == "missing_metadata", "legacy punchline object is unsupported")

    ok(contract_hash() == "a40b144e0098c26b2bf578cc8fbebe018a798d3cfbc7399f4e9668a857f01314", "single compatibility hash")
    validate_contract_hash(contract_hash())
    try:
        validate_contract_hash("0" * 64)
    except ValueError:
        ok(True, "compatibility mismatch fails")
    else:
        raise AssertionError("compatibility mismatch must fail")

    body = build_upload_body(request)
    ok(body["status"]["privacyStatus"] == "public" and "publishAt" not in body["status"], "immediate PUBLIC body")

    scheduled = json.loads(json.dumps(request))
    scheduled["publish_at"] = "2030-01-01T00:00:00Z"
    try:
        validate_request_data(scheduled)
    except ValueError:
        ok(True, "scheduling fields rejected")
    else:
        raise AssertionError("scheduled field must fail")

    ok(evidence_path(request["content_id"], "intent").startswith("content/executions/evidence/"), "new execution evidence path")
    ok(result_path(request["content_id"]) == f"content/results/{request['content_id']}.json", "immutable result path")
    ok(CONTRACT["request_path"] == "content/requests/{content_id}.json", "contract descriptor")

    single = (ROOT / ".github/workflows/single.yml").read_text(encoding="utf-8")
    ok(all(name in single for name in ("execution_id", "source_sha", "contract_hash", "dispatch_id")), "opaque dispatch inputs")
    ok("narration" not in single and "request_json" not in single, "no full request workflow input")

    stale = []
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".yml", ".yaml", ".md"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if ("youtube" + "-shorts-bot/") in text:
                stale.append(path.as_posix())
    ok(not stale, "no legacy private path references")

    forbidden = [
        ROOT / ".github/workflows/run.yml",
        ROOT / ".github/workflows/observe.yml",
        ROOT / ".github/workflows/review-evidence.yml",
        ROOT / "runtime/observe.py",
    ]
    ok(not any(path.exists() for path in forbidden), "no Daily/analytics runtime workflows")
    ok("LEGACY_CONTRACT_HASHES" not in (ROOT / "runtime/base/compat.py").read_text(encoding="utf-8"), "no legacy contract hashes")

    state = EmptyState()
    decision = prepare_upload(
        state,
        "runtime/content/requests/" + request["content_id"] + ".json",
        request,
        {
            "content_id": request["content_id"],
            "request_path": f"content/requests/{request['content_id']}.json",
            "request_blob_sha": "a" * 40,
            "source_commit_sha": "b" * 40,
        },
        FakeYoutube(),
    )
    ok(decision["upload_required"] is True and not state.created, "intent not created during render preparation")

    transfer_source = (ROOT / "runtime/output/transfer.py").read_text(encoding="utf-8")
    intent_pos = transfer_source.index('state.create(evidence_path(identity["content_id"],"intent")')
    insert_pos = transfer_source.index("youtube.videos().insert")
    ok(intent_pos < insert_pos, "durable intent precedes videos.insert")
    ok("duplicate upload forbidden" in transfer_source, "ambiguous upload blocks")
    result_source = (ROOT / "runtime/output/result.py").read_text(encoding="utf-8")
    compact_result = result_source.replace(" ", "")
    ok('"visibility":"public"' in compact_result and 'verification.get("passed")isnotTrue' in compact_result, "result requires verified PUBLIC")
    resolver_source = (ROOT / "runtime/resources/resolve.py").read_text(encoding="utf-8")
    ok(resolver_source.count("setsar=1") >= 3, "background normalization forces square pixels")
    ok("stream_loop" in (ROOT / "runtime/transform/process.py").read_text(encoding="utf-8") and "loop_count" in resolver_source, "no-loop treatment is explicit")
    ok(
        "max_source_duration = output * fit_limit" in resolver_source
        and "used_duration = min(source_duration, max_source_duration)" in resolver_source
        and "if source_trimmed:" in resolver_source,
        "overlong backgrounds trim before bounded speed fit",
    )
    ok(
        '"background_treatment_source_trimmed": source_trimmed' in resolver_source,
        "background trimming is observable",
    )
    print(f"SELF_TEST_PASS checks={checks}")
    print("contract_hash=" + contract_hash())


if __name__ == "__main__":
    main()
