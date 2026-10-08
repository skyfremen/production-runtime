"""Isolated Zodiac editorial handoff intake (Step 7).

This lane cannot render, dispatch, upload, schedule or access channel state.
It deliberately imports nothing from the existing Wacky production engine.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys

CONTRACT = "wacky-astrology-handoff-v1"
LANE = "zodiac"
SOURCE = "skyfremen/zodiac-workflow"
MAX_INPUT_BYTES = 2_000_000
FAMILIES = frozenset({
    "find_your_sign", "angel_vs_devil", "zodiac_ranking", "birth_month_hunt",
    "relationship_matching", "choose_before_reveal",
    "zodiac_traits", "element_comparison",
})
SIGNS = frozenset(
    "aries taurus gemini cancer leo virgo libra scorpio sagittarius capricorn aquarius pisces".split()
)
MONTHS = frozenset(
    "january february march april may june july august september october november december".split()
)
SCORES = {"desire_self": 25, "desire_other": 20, "payoff": 20, "clarity": 15,
          "readability": 10, "freshness": 10}
WACKY_CREDS = (
    "PRIVATE_STATE_TOKEN", "PRIVATE_STATE_REPOSITORY", "RUNTIME_AUTH_A",
    "RUNTIME_AUTH_B", "RUNTIME_AUTH_C", "PUBLIC_PRODUCTION_TOKEN",
)
ROOT_FIELDS = frozenset({
    "contract", "schema_version", "lane", "source_repository", "source_revision",
    "purpose", "output_spec", "permissions", "editorial_audit", "requests",
    "payload_sha256",
})


class HandoffRejected(ValueError):
    pass


def check(condition, message):
    if not condition:
        raise HandoffRejected(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def checksum(value):
    return sha256(canonical(value)).hexdigest()


def plain(value):
    return isinstance(value, str) and bool(value.strip())


def norm(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))


def validate_request(item):
    check(isinstance(item, dict) and set(item) == {"concept_id", "creative", "creative_sha256"},
          "request must contain only concept_id, creative, creative_sha256")
    cid = item["concept_id"]
    check(plain(cid) and re.fullmatch(r"za-[a-z0-9-]{8,64}", cid) is not None,
          "Zodiac concept_id namespace required")
    creative = item["creative"]
    check(isinstance(creative, dict), "creative must be an object")
    check(creative.get("concept_id") == cid, "request/concept_id mismatch")
    check(item["creative_sha256"] == checksum(creative), "creative integrity mismatch")
    check(creative.get("format_family") in FAMILIES, "invalid Zodiac family")
    check(plain(creative.get("task_prompt")) and
          plain(creative.get("first_view_payoff")) and
          plain(creative.get("loop_transition")) and
          plain(creative.get("proposed_title")), "creative requires an original, complete first-view story")
    duration = creative.get("duration_seconds")
    check(type(duration) in (int, float) and 3 <= duration <= 120, "invalid duration")
    coverage = creative.get("target_identity_and_coverage")
    check(isinstance(coverage, dict), "missing identity coverage")
    kind = coverage.get("kind")
    identities = coverage.get("identities")
    results = coverage.get("results")
    check(kind in {"sign", "month", "choice", "match", "subset"} and
          isinstance(identities, list) and len(identities) >= 2 and
          all(plain(n) for n in identities) and
          len(set(norm(n) for n in identities)) == len(identities) and
          isinstance(results, dict) and
          set(norm(n) for n in identities) == set(norm(k) for k in results) and
          all(plain(v) for v in results.values()) and
          len(set(norm(v) for v in results.values())) >= 2, "incomplete or interchangeable results")
    if coverage.get("universal") is True:
        expected = SIGNS if kind in {"sign", "match"} else MONTHS if kind == "month" else None
        check(expected is not None and {norm(n) for n in identities} == expected,
              "universal promise requires all twelve identities")
    else:
        check(coverage.get("universal") is False and plain(coverage.get("subset_label")) and
              norm(coverage["subset_label"]) in norm(creative["task_prompt"]),
              "declared subset must appear in the hook")
    task = norm(creative["task_prompt"])
    if "your sign" in task or "your zodiac" in task:
        check(kind in {"sign", "match"} and coverage.get("universal") is True,
              "universal sign promise unfulfilled")
    if "your month" in task or "your birth month" in task:
        check(kind == "month" and coverage.get("universal") is True,
              "universal month promise unfulfilled")
    scenes = creative.get("timed_scenes")
    display_mode = creative.get("display_mode")
    check(display_mode in (None, "full_screen_list"), "unknown display mode")
    if display_mode == "full_screen_list":
        check(creative.get("list_style") in {
            "sign_results", "ranking", "grouped_elements", "trait_matches"
        }, "invalid list template")
        check(isinstance(scenes, list) and len(scenes) == 1,
              "one full-screen list scene required")
        scene = scenes[0]
        check(isinstance(scene, dict) and scene.get("kind") == "full_list" and
              scene.get("start") == 0 and scene.get("end") == duration and
              type(scene.get("stable_seconds")) in (int,float) and
              scene["stable_seconds"] >= duration-.25 and
              type(scene.get("reading_load_words")) is int and
              scene["reading_load_words"] >= 1,
              "list must remain stable for complete video")
        texts = scene.get("visible_text")
        check(isinstance(texts, list) and
              all(plain(s) for s in texts) and
              norm(creative["task_prompt"]) in norm(" ".join(texts)),
              "first-frame title missing")
        for identity, result in results.items():
            check(norm(identity+" "+result) in norm(" ".join(texts)),
                  "first-frame list entry missing: "+identity)
        check(2 <= len(identities) <= 18, "list size not readable")
        check(duration >= 6, "list needs reading time")
        check(not (creative["list_style"] == "grouped_elements" and
                   {norm(s) for s in identities} != SIGNS),
              "element grouping needs every sign")
    else:
        check(isinstance(scenes, list) and len(scenes) >= 3, "timed scenes missing")
    if display_mode != "full_screen_list":
        _check_legacy_scenes(creative, scenes, duration, results)
    # The remaining fields remain binding for both full-screen and legacy layouts.
    scores = creative.get("score_breakdown")
    check(isinstance(scores, dict) and set(scores) == set(SCORES) and
          all(type(scores[k]) is int and 0 <= scores[k] <= upper for k, upper in SCORES.items()) and
          sum(scores.values()) >= 78, "low or malformed scores")
    options = creative.get("opening_variant_decision")
    check(isinstance(options, dict) and isinstance(options.get("options"), list) and
          len(options["options"]) in (1, 2, 3) and
          type(options.get("selected_index")) is int and
          0 <= options["selected_index"] < len(options["options"]), "opening audit missing")
    selected = options["options"][options["selected_index"]]
    check(isinstance(selected, dict) and plain(selected.get("promise")) and
          norm(selected["promise"]) in task, "opening promise unfulfilled")
    feasibility = creative.get("render_feasibility")
    check(isinstance(feasibility, dict) and
          all(feasibility.get(k) is True for k in
              ("phone_readable", "muted", "complete_first_view", "assets_owned_or_licensed")) and
          plain(feasibility.get("timing_reason")), "editorial feasibility evidence missing")
    return {"concept_id": cid, "editorial": creative,
            "duration_seconds": duration, "status": "step7_validated_handoff_only"}



def _check_legacy_scenes(creative, scenes, duration, results):
    task = norm(creative["task_prompt"])
    t = 0.
    kinds = []
    for scene in scenes:
        check(isinstance(scene, dict), "bad scene")
        start, end = scene.get("start"), scene.get("end")
        stable, load = scene.get("stable_seconds"), scene.get("reading_load_words")
        texts = scene.get("visible_text")
        check(type(start) in (int, float) and type(end) in (int, float) and
              abs(start-t) < .01 and end > start and end <= duration + .01 and
              type(stable) in (int, float) and 0 <= stable <= end-start+.01 and
              type(load) is int and load >= 0 and isinstance(texts, list) and
              all(plain(s) for s in texts), "broken scene timeline/reading metadata")
        check(scene.get("kind") in {"hook", "lookup", "payoff", "hold"}, "invalid scene kind")
        if scene["kind"] in {"lookup", "payoff"}:
            check(stable >= max(1.5, load/3.0), "insufficient reading time")
        kinds.append(scene["kind"])
        t = end
    check(abs(t-duration) < .01 and kinds[0] == "hook" and "lookup" in kinds and
          "payoff" in kinds and kinds[-1] == "hold" and
          scenes[-1]["stable_seconds"] >= 0.75, "incomplete silent sequence")
    check(task in norm(" ".join(scenes[0]["visible_text"])), "hook not visible at frame zero")
    for identity, result in results.items():
        target = norm(identity + " " + result)
        min_hold = max(1.5, len(target.split())/3.0)
        check(any(sc["kind"] in {"lookup", "payoff", "hold"} and
                  target in norm(" ".join(sc["visible_text"])) and
                  sc["stable_seconds"] >= min_hold for sc in scenes[1:]),
              "identity result missing or unreadable: " + str(identity))

def validate_envelope(envelope):
    check(isinstance(envelope, dict) and set(envelope) == ROOT_FIELDS,
          "unknown or missing handoff root fields")
    check(envelope["contract"] == CONTRACT and
          type(envelope["schema_version"]) is int and
          envelope["schema_version"] == 1 and
          envelope["lane"] == LANE and
          envelope["source_repository"] == SOURCE and
          envelope["purpose"] == "editorial_visual_handoff_only",
          "not the dedicated Zodiac planning contract")
    check(isinstance(envelope["source_revision"], str) and
          re.fullmatch(r"[0-9a-f]{40}", envelope["source_revision"]) is not None,
          "source revision must be a full commit SHA")
    spec = envelope["output_spec"]
    check(spec == {"width": 1080, "height": 1920, "fps": 30, "narration": False},
          "wrong visual output spec")
    check(envelope["permissions"] == {"render": False, "upload": False,
                                     "publish": False, "schedule": False},
          "all potentially active permissions must be disabled")
    audit = envelope["editorial_audit"]
    check(isinstance(audit, dict) and set(audit) ==
          {"requested_winners", "selected_count", "distinct_pool", "scores"} and
          type(audit["requested_winners"]) is int and
          1 <= audit["requested_winners"] <= 10 and
          type(audit["selected_count"]) is int and
          audit["selected_count"] == audit["requested_winners"] and
          type(audit["distinct_pool"]) is int and
          audit["distinct_pool"] >= audit["requested_winners"]*15,
          "editorial portfolio proof incomplete")
    requests = envelope["requests"]
    check(isinstance(requests, list) and len(requests) == audit["requested_winners"],
          "request count differs from approved editorial count")
    check(envelope["payload_sha256"] == checksum({k: v for k, v in envelope.items()
                                                 if k != "payload_sha256"}),
          "handoff checksum mismatch")
    output = [validate_request(r) for r in requests]
    ids = [r["concept_id"] for r in output]
    check(len(ids) == len(set(ids)) and
          set(audit["scores"]) == set(ids) and
          all(type(audit["scores"][r["concept_id"]]) is int and
              audit["scores"][r["concept_id"]] ==
              sum(r["editorial"]["score_breakdown"].values()) for r in output),
          "duplicate concept or mismatched score audit")
    return {
        "contract": CONTRACT, "schema_version": 1, "lane": LANE,
        "source_repository": SOURCE, "source_revision": envelope["source_revision"],
        "handoff_sha256": envelope["payload_sha256"],
        "mode": "validation_only",
        "render_enabled": False, "upload_enabled": False,
        "publish_enabled": False, "schedule_enabled": False,
        "ready_for_render": False, "verified_video_files": [],
        "validated_requests": output,
    }


def run(input_path, output_path):
    check(not any(os.environ.get(k) for k in WACKY_CREDS),
          "Wacky production credentials cannot enter Zodiac validation")
    raw = Path(input_path).read_bytes()
    check(len(raw) <= MAX_INPUT_BYTES, "handoff exceeds input limit")
    envelope = json.loads(raw.decode("utf-8"))
    result = validate_envelope(envelope)
    out = Path(output_path)
    check(out.resolve() != Path(input_path).resolve(),
          "must not overwrite source request")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False,
                              sort_keys=True, indent=2)+"\n", encoding="utf-8")
    tmp.replace(out)
    return result


def main(argv=None):
    cli = argparse.ArgumentParser(description="Zodiac isolated offline execution intake. Never uploads.")
    cli.add_argument("--input", required=True)
    cli.add_argument("--output", required=True)
    args = cli.parse_args(argv)
    try:
        result = run(args.input, args.output)
        print(json.dumps({
            "status": result["mode"],
            "lane": result["lane"],
            "validated": len(result["validated_requests"]),
            "render_enabled": False, "upload_enabled": False,
            "output": str(args.output),
        }, sort_keys=True))
        return 0
    except (HandoffRejected, OSError, ValueError, UnicodeError, TypeError, KeyError) as err:
        print(f"ZODIAC_HANDOFF_REJECTED: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
