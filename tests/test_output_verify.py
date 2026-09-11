import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from output import verify
from output.state import RecoveryBlocked
from output.verify import RETRY_DELAYS


class VerificationPollingTests(unittest.TestCase):
    def test_polling_keeps_same_bound_without_long_blind_spot(self):
        self.assertEqual(RETRY_DELAYS[0], 0)
        self.assertEqual(sum(RETRY_DELAYS), 60)
        self.assertLessEqual(max(RETRY_DELAYS), 10)
        self.assertGreater(len(RETRY_DELAYS), 6)
        cumulative = []
        elapsed = 0
        for delay in RETRY_DELAYS:
            elapsed += delay
            cumulative.append(elapsed)
        self.assertIn(22, cumulative)
        self.assertIn(26, cumulative)
        self.assertIn(30, cumulative)

    def test_deterministic_mismatch_writes_non_retryable_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "classification.json"
            with patch.object(verify, "FAILURE_CLASSIFICATION", target), patch.object(
                verify, "main", side_effect=RecoveryBlocked("mismatch")
            ):
                with self.assertRaises(RecoveryBlocked):
                    verify.guarded_main()
            payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["error_code"], "E_VERIFY_001")
        self.assertFalse(payload["retryable"])


if __name__ == '__main__':
    unittest.main()
