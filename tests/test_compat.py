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


if __name__ == "__main__":
    unittest.main()
