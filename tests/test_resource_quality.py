import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from resources.quality import MAX_PROTECTION_ALPHA, protection_for_samples


class CaptionRegionQualityTests(unittest.TestCase):
    def test_dark_stable_source_uses_subtle_protection(self):
        self.assertEqual(protection_for_samples([70] * 12, [130] * 12, [2] * 12), 0.18)

    def test_bright_source_strengthens_protection(self):
        self.assertEqual(protection_for_samples([210] * 12, [250] * 12, [3] * 12), MAX_PROTECTION_ALPHA)

    def test_dark_to_bright_change_strengthens_protection(self):
        self.assertEqual(protection_for_samples([70, 190], [130, 225], [0, 40]), MAX_PROTECTION_ALPHA)


if __name__ == "__main__":
    unittest.main()
