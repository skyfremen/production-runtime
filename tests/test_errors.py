import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core
import transport


class ErrorModelTests(unittest.TestCase):
    def test_execution_failure_is_publicly_generic_and_privately_detailed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "diagnostic.json"
            with patch.object(core, "DIAGNOSTIC", target):
                try:
                    raise core.RunnerError("owner-only detail")
                except core.RunnerError as exc:
                    code = core.record_failure(exc)
            payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(code, "E_EXEC_001")
        self.assertEqual(payload["error_code"], code)
        self.assertIn("owner-only detail", payload["detail"])
        self.assertTrue(payload["execution_started"])
        self.assertFalse(payload["verification_completed"])

    def test_boundary_failure_classes_have_stable_codes(self):
        self.assertEqual(transport.failure_code("fetch"), "E_LOAD_001")
        self.assertEqual(transport.failure_code("complete"), "E_FINALIZE_001")
        self.assertEqual(transport.failure_code("diagnose"), "E_FINALIZE_001")

    def test_bad_source_revision_fails_before_network_access(self):
        with self.assertRaisesRegex(transport.TransportError, "Invalid opaque"):
            transport.bootstrap("b_" + "0" * 30, "bad", "/tmp/unused")

    def test_missing_batch_is_classified_as_load_failure(self):
        state = unittest.mock.Mock()
        state.content.side_effect = transport.TransportError("missing")
        with self.assertRaises(transport.TransportError):
            transport.recovery_batch(state, "r_" + "0" * 30, "1" * 40)
        self.assertEqual(transport.failure_code("fetch"), "E_LOAD_001")

    def test_finalization_failure_is_classified_without_public_detail(self):
        self.assertEqual(transport.failure_code("complete"), "E_FINALIZE_001")


if __name__ == "__main__":
    unittest.main()
