"""Offline Zodiac handoff boundary tests. No secrets, provider API or media."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zodiac import entrypoint as z

ROOT = Path(__file__).resolve().parents[2]


def sample_creative(idx=0):
    cid = f"za-runtime-case-{idx:04d}"
    title = f"Which secret elevator door is yours in castle {idx}?"
    task = f"Aries vs Taurus: {title}"
    one = "red elevator opens a cloud door"
    two = "blue elevator opens an ocean door"
    return {
        "concept_id": cid, "format_family": "find_your_sign",
        "duration_seconds": 10,
        "task_prompt": task,
        "scenario_and_desired_result": "A fictional magic elevator assigns two funny doors",
        "first_view_payoff": f"Aries takes the cloud door; Taurus takes the ocean door, castle {idx}.",
        "loop_transition": "Elevator returns to its first-frame position without a blank screen.",
        "proposed_title": title,
        "target_identity_and_coverage": {
            "kind": "subset", "universal": False, "subset_label": "Aries vs Taurus",
            "identities": ["aries", "taurus"], "results": {"aries": one, "taurus": two},
        },
        "timed_scenes": [
            {"start": 0, "end": 2, "kind": "hook",
             "visible_text": [task], "stable_seconds": 2, "reading_load_words": 6},
            {"start": 2, "end": 6, "kind": "lookup",
             "visible_text": ["Aries: " + one, "Taurus: " + two],
             "stable_seconds": 4, "reading_load_words": 8},
            {"start": 6, "end": 8, "kind": "payoff",
             "visible_text": ["Magic elevator doors reveal the surprise"],
             "stable_seconds": 2, "reading_load_words": 6},
            {"start": 8, "end": 10, "kind": "hold",
             "visible_text": ["Aries: " + one, "Taurus: " + two],
             "stable_seconds": 2, "reading_load_words": 0},
        ],
        "score_breakdown": {
            "desire_self": 23, "desire_other": 19, "payoff": 20,
            "clarity": 14, "readability": 10, "freshness": 10,
        },
        "opening_variant_decision": {
            "options": [{"promise": "Aries vs Taurus", "composition": "two doors",
                         "why_distinct": "shows both possible outcomes"}],
            "selected_index": 0, "selection_reason": "Immediate contrast",
            "fewer_reason": "One honest framing for this particular tiny test",
        },
        "render_feasibility": {
            "phone_readable": True, "muted": True,
            "complete_first_view": True, "assets_owned_or_licensed": True,
            "timing_reason": "Two outcomes are held legibly for four seconds.",
            "risks": ["Physical visual QC not yet performed"],
        },
    }


def envelope(count=2):
    items = []
    scores = {}
    for i in range(count):
        creative = sample_creative(i)
        cid = creative["concept_id"]
        items.append({"concept_id": cid, "creative": creative,
                      "creative_sha256": z.checksum(creative)})
        scores[cid] = sum(creative["score_breakdown"].values())
    e = {
        "contract": z.CONTRACT, "schema_version": 1, "lane": "zodiac",
        "source_repository": "skyfremen/zodiac-workflow",
        "source_revision": "a"*40,
        "purpose": "editorial_visual_handoff_only",
        "output_spec": {"width": 1080, "height": 1920, "fps": 30, "narration": False},
        "permissions": {"render": False, "upload": False, "publish": False,
                        "schedule": False},
        "editorial_audit": {
            "requested_winners": count, "selected_count": count,
            "distinct_pool": count*15, "scores": scores,
        }, "requests": items,
    }
    e["payload_sha256"] = z.checksum(e)
    return e


def resign(e):
    for r in e["requests"]:
        r["creative_sha256"] = z.checksum(r["creative"])
    e["payload_sha256"] = z.checksum({k: v for k, v in e.items()
                                    if k != "payload_sha256"})
    return e


class ZodiacIsolationTests(unittest.TestCase):
    def test_two_valid_isolated_requests(self):
        result = z.validate_envelope(envelope())
        self.assertEqual(result["mode"], "validation_only")
        self.assertEqual(result["lane"], "zodiac")
        self.assertEqual(len(result["validated_requests"]), 2)
        for field in ("render_enabled", "upload_enabled", "publish_enabled",
                      "schedule_enabled", "ready_for_render"):
            self.assertIs(result[field], False)
        self.assertEqual(result["verified_video_files"], [])

    def test_wacky_manifest_cannot_enter_zodiac_lane(self):
        with self.assertRaises(z.HandoffRejected):
            z.validate_envelope({"manifest_version": 2,
                                 "execution_id": "ex-" + "a"*24,
                                 "requests": []})

    def test_mutated_editorial_text_checksum_rejected(self):
        e = envelope()
        e["requests"][0]["creative"]["proposed_title"] = "tampered"
        with self.assertRaisesRegex(z.HandoffRejected, "checksum"):
            z.validate_envelope(e)

    def test_wrong_repo_and_revision_rejected(self):
        for field, value in (("source_repository", "skyfremen/youtube-workflow"),
                             ("source_revision", "main"),
                             ("source_revision", "a"*39)):
            e = envelope()
            e[field] = value
            resign(e)
            with self.subTest(field=field, value=value):
                with self.assertRaises(z.HandoffRejected):
                    z.validate_envelope(e)

    def test_permissions_fail_closed_even_with_recomputed_hash(self):
        for key in ("render", "upload", "publish", "schedule"):
            e = envelope()
            e["permissions"][key] = True
            resign(e)
            with self.subTest(key=key):
                with self.assertRaisesRegex(z.HandoffRejected, "permission"):
                    z.validate_envelope(e)

    def test_wacky_concept_id_rejected(self):
        e = envelope()
        e["requests"][0]["concept_id"] = "wd-" + "a"*24
        e["requests"][0]["creative"]["concept_id"] = e["requests"][0]["concept_id"]
        resign(e)
        with self.assertRaises(z.HandoffRejected):
            z.validate_envelope(e)

    def test_no_missing_result_even_with_valid_checksums(self):
        e = envelope()
        e["requests"][0]["creative"]["target_identity_and_coverage"]["results"].pop("aries")
        resign(e)
        with self.assertRaisesRegex(z.HandoffRejected, "results"):
            z.validate_envelope(e)

    def test_fast_flash_even_with_valid_checksums_rejected(self):
        e = envelope()
        for scene in e["requests"][0]["creative"]["timed_scenes"]:
            if scene["kind"] in ("lookup", "hold"):
                scene["stable_seconds"] = 1.0
        resign(e)
        with self.assertRaises(z.HandoffRejected):
            z.validate_envelope(e)

    def test_count_mismatch_rejected(self):
        e = envelope()
        e["editorial_audit"]["requested_winners"] = 3
        resign(e)
        with self.assertRaises(z.HandoffRejected):
            z.validate_envelope(e)

    def test_duplicate_ids_rejected(self):
        e = envelope()
        e["requests"][1] = deepcopy(e["requests"][0])
        resign(e)
        with self.assertRaises(z.HandoffRejected):
            z.validate_envelope(e)

    def test_spoofed_editorial_score_rejected(self):
        e = envelope()
        e["editorial_audit"]["scores"][e["requests"][0]["concept_id"]] = 81
        resign(e)
        with self.assertRaises(z.HandoffRejected):
            z.validate_envelope(e)

    def test_no_secret_inheritance(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td)/"source.json"
            out = Path(td)/"output.json"
            inp.write_text(json.dumps(envelope()), encoding="utf8")
            with patch.dict(os.environ, {"PRIVATE_STATE_TOKEN": "DO_NOT_USE"}):
                with self.assertRaisesRegex(z.HandoffRejected, "credentials"):
                    z.run(inp, out)
            self.assertFalse(out.exists())

    def test_cli_writes_only_validation_record(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td)/"source.json"
            out = Path(td)/"output.json"
            inp.write_text(json.dumps(envelope()), encoding="utf8")
            with patch.dict(os.environ, {k: "" for k in z.WACKY_CREDS}):
                self.assertEqual(z.main(["--input", str(inp), "--output", str(out)]), 0)
            result = json.loads(out.read_text(encoding="utf8"))
            self.assertEqual(len(result["validated_requests"]), 2)
            self.assertFalse(result["render_enabled"])
            self.assertFalse(result["upload_enabled"])

    def test_legacy_wacky_entrypoints_untouched(self):
        old = ROOT / "runtime/core.py"
        self.assertIn("from engine.pipeline import ProductionPipeline",
                      old.read_text(encoding="utf8"))
        old_workflow = ROOT / ".github/workflows/single.yml"
        self.assertIn("runtime/core.py", old_workflow.read_text(encoding="utf8"))
        current = (ROOT/"runtime/zodiac/entrypoint.py").read_text(encoding="utf8")
        for banned in ("from engine.pipeline", "output.execute", "youtube.upload",
                       "PRIVATE_STATE_TOKEN ="):
            self.assertNotIn(banned, current)

    def test_no_network_in_zodiac_runtime(self):
        code = (ROOT/"runtime/zodiac/entrypoint.py").read_text(encoding="utf8")
        for forbidden in ("urlopen(", "requests.post(", "googleapiclient",
                          "workflow_dispatch", "subprocess.run(", "os.system("):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
