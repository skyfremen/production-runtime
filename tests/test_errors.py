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
            internal = Path(tmp) / "internal.log"
            internal.write_text("underlying private failure\n", encoding="utf-8")
            with patch.object(core, "DIAGNOSTIC", target), patch.object(
                core, "INTERNAL_LOG", internal
            ):
                try:
                    raise core.RunnerError("owner-only detail")
                except core.RunnerError as exc:
                    code = core.record_failure(exc)
            payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(code, "E_EXEC_001")
        self.assertEqual(payload["error_code"], code)
        self.assertIn("owner-only detail", payload["detail"])
        self.assertIn("underlying private failure", payload["detail"])
        self.assertTrue(payload["execution_started"])
        self.assertFalse(payload["verification_completed"])

    def test_deterministic_verification_failure_is_not_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "diagnostic.json"
            internal = Path(tmp) / "internal.log"
            classification = Path(tmp) / "classification.json"
            classification.write_text(json.dumps({
                "stage": "verify",
                "error_code": "E_VERIFY_001",
                "retryable": False,
            }), encoding="utf-8")
            with patch.object(core, "DIAGNOSTIC", target), patch.object(
                core, "INTERNAL_LOG", internal
            ), patch.object(core, "FAILURE_CLASSIFICATION", classification):
                try:
                    raise core.RunnerError("aggregate")
                except core.RunnerError as exc:
                    code = core.record_failure(exc)
            payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(code, "E_VERIFY_001")
        self.assertEqual(payload["stage"], "verify")
        self.assertFalse(payload["retryable"])

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
