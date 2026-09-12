import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from output import access as auth_preflight
from output.state import RecoveryBlocked


CREDS = {
    "RUNTIME_AUTH_A": "client",
    "RUNTIME_AUTH_B": "secret",
    "RUNTIME_AUTH_C": "refresh",
}
CHANNEL = {"id": "UCvrq2m9G4yrwPfL_X-QPzMA"}


class FakeHttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.resp = Mock(status=status)


class ExternalReadinessPreflightTests(unittest.TestCase):
    def test_missing_credentials_fail_before_client_or_network(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("output.access.make_client") as make_client:
            with self.assertRaisesRegex(SystemExit, "missing RUNTIME_AUTH_A"):
                auth_preflight.run_preflight()
        make_client.assert_not_called()

    def test_success_reuses_production_client_and_pinned_identity_check(self):
        client = object()
        with patch.dict(os.environ, CREDS, clear=True), \
             patch("output.access.make_client", return_value=client) as make_client, \
             patch("output.access.authenticated_channel", return_value=CHANNEL) as identity_check:
            result = auth_preflight.run_preflight()
        self.assertEqual(result, CHANNEL)
        make_client.assert_called_once_with()
        identity_check.assert_called_once_with(client)

    def test_identity_mismatch_is_distinguished(self):
        with patch.dict(os.environ, CREDS, clear=True), \
             patch("output.access.make_client", return_value=object()), \
             patch("output.access.authenticated_channel", side_effect=RecoveryBlocked("different identity")):
            with self.assertRaisesRegex(SystemExit, "identity readiness.*different identity"):
                auth_preflight.run_preflight()

    def test_auth_permission_quota_and_transient_api_failures_are_classified(self):
        cases = [
            (401, "invalid credentials", "authentication"),
            (403, "access forbidden", "permission/API configuration"),
            (403, "quotaExceeded", "quota"),
            (503, "backend unavailable", "transient external API"),
        ]
        for status, detail, expected in cases:
            with self.subTest(status=status, detail=detail), \
                 patch.dict(os.environ, CREDS, clear=True), \
                 patch("output.access.make_client", return_value=object()), \
                 patch("output.access.authenticated_channel", side_effect=FakeHttpError(status, detail)):
                with self.assertRaisesRegex(SystemExit, expected):
                    auth_preflight.run_preflight()

    def test_preflight_source_has_no_remote_mutation_path(self):
        source = (BASE / "output/access.py").read_text(encoding="utf-8")
        for forbidden in ("videos().insert", "upload_new(", "execute_upload(", "state.create("):
            self.assertNotIn(forbidden, source)
        self.assertIn("authenticated_channel(make_client())", source)


if __name__ == "__main__":
    unittest.main()
