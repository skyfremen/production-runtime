import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from base import compat
from base import contract
from resources import policy


class CompatibilityContractTests(unittest.TestCase):
    def test_fingerprint_is_stable_sha256(self):
        first = compat.contract_hash()
        second = compat.contract_hash()
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_output_and_media_targets_are_internally_aligned(self):
        payload = compat.contract_payload()
        self.assertEqual(payload["base"]["output"]["width"], policy.TARGET_WIDTH)
        self.assertEqual(payload["base"]["output"]["height"], policy.TARGET_HEIGHT)
        self.assertEqual(payload["base"]["output"]["fps"], policy.TARGET_FPS)
        self.assertEqual(contract.DEFAULT_VIDEO_WIDTH, 1080)
        self.assertEqual(contract.DEFAULT_VIDEO_HEIGHT, 1920)
        self.assertEqual(contract.DEFAULT_VIDEO_FPS, 30)

    def test_current_fingerprint_is_supported(self):
        current = compat.contract_hash()
        self.assertEqual(compat.validate_contract_hash(current), current)
        self.assertIn(current, compat.supported_contract_hashes())

    def test_pre_v5_fingerprint_remains_supported_for_staged_rollout(self):
        current = compat.contract_hash()
        self.assertEqual(len(compat.LEGACY_CONTRACT_HASHES), 1)
        legacy = next(iter(compat.LEGACY_CONTRACT_HASHES))
        self.assertRegex(legacy, r"^[0-9a-f]{64}$")
        self.assertNotEqual(legacy, current)
        self.assertEqual(compat.validate_contract_hash(legacy), legacy)
        self.assertIn(legacy, compat.supported_contract_hashes())

    def test_fail_closed_for_missing_invalid_or_unknown_fingerprint(self):
        with self.assertRaises(ValueError):
            compat.validate_contract_hash("")
        with self.assertRaises(ValueError):
            compat.validate_contract_hash("not-a-hash")
        with self.assertRaises(ValueError):
            compat.validate_contract_hash("0" * 64)

    def test_staged_rollout_can_temporarily_accept_legacy_empty_only(self):
        self.assertEqual(
            compat.validate_contract_hash("", allow_legacy_empty=True),
            "",
        )
        with self.assertRaises(ValueError):
            compat.validate_contract_hash("0" * 64, allow_legacy_empty=True)

    def test_behavioral_vectors_are_present(self):
        payload = compat.contract_payload()
        self.assertTrue(payload["behavior"]["marker"].startswith("wd-id-"))
        self.assertEqual(payload["behavior"]["voices"]["female:dramatic"], "af_bella")
        self.assertEqual(payload["behavior"]["voices"]["male:natural"], "am_echo")
        self.assertTrue(payload["behavior"]["renditions"]["exact_vertical"]["suitable"])
        self.assertFalse(payload["behavior"]["renditions"]["hd_landscape"]["suitable"])
        self.assertTrue(payload["behavior"]["renditions"]["uhd_landscape"]["suitable"])

    def test_semantic_and_background_treatment_schema_are_fingerprinted(self):
        schema = compat.contract_payload()["schema"]
        self.assertEqual(schema["current_version"], 5)
        self.assertEqual(schema["supported_versions"], [4, 5])
        self.assertEqual(schema["punchline_required_keys"], ["emphasis_text", "text"])
        self.assertEqual(schema["punchline_optional_keys"], ["type"])
        self.assertEqual(schema["punchline_max_emphasis_words"], 5)
        self.assertIn("REVERSAL", schema["punchline_types"])
        self.assertIn("punchline", schema["story_keys"])
        self.assertIn("background_primary_treatment", schema["visual_keys"])
        self.assertIn("background_backup_treatment", schema["visual_keys"])
        self.assertEqual(
            schema["treatment_keys"],
            ["playback_rate", "segment_duration_seconds", "segment_start_seconds"],
        )
        self.assertEqual(schema["playback_rate_min"], 1.0)
        self.assertEqual(schema["playback_rate_max"], 2.0)

    def test_production_boundary_requires_fingerprint(self):
        workflow = (ROOT / ".github" / "workflows" / "run.yml").read_text(encoding="utf-8")
        contract_section = workflow.split("contract_hash:", 1)[1].split("concurrency:", 1)[0]
        self.assertIn("required: true", contract_section)
        self.assertNotIn("default: ''", contract_section)
        self.assertIn('validate_contract_hash(os.environ.get("CONTRACT_HASH"))', workflow)
        self.assertNotIn("allow_legacy_empty=True", workflow)


if __name__ == "__main__":
    unittest.main()
