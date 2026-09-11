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
    "YOUTUBE_CLIENT_ID": "client",
    "YOUTUBE_CLIENT_SECRET": "secret",
    "YOUTUBE_REFRESH_TOKEN": "refresh",
}
CHANNEL = {"id": "UCvrq2m9G4yrwPfL_X-QPzMA"}


class FakeHttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.resp = Mock(status=status)


class YouTubeReadinessPreflightTests(unittest.TestCase):
    def test_missing_credentials_fail_before_client_or_network(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("output.access.make_client") as make_client:
            with self.assertRaisesRegex(SystemExit, "missing YOUTUBE_CLIENT_ID"):
                auth_preflight.run_preflight()
        make_client.assert_not_called()

    def test_success_reuses_production_client_and_pinned_channel_check(self):
        client = object()
        with patch.dict(os.environ, CREDS, clear=True), \
             patch("output.access.make_client", return_value=client) as make_client, \
             patch("output.access.authenticated_channel", return_value=CHANNEL) as channel_check:
            result = auth_preflight.run_preflight()
        self.assertEqual(result, CHANNEL)
        make_client.assert_called_once_with()
        channel_check.assert_called_once_with(client)

    def test_channel_mismatch_is_distinguished(self):
        with patch.dict(os.environ, CREDS, clear=True), \
             patch("output.access.make_client", return_value=object()), \
             patch("output.access.authenticated_channel", side_effect=RecoveryBlocked("different channel")):
            with self.assertRaisesRegex(SystemExit, "channel readiness.*different channel"):
                auth_preflight.run_preflight()

    def test_auth_permission_quota_and_transient_api_failures_are_classified(self):
        cases = [
            (401, "invalid credentials", "authentication"),
            (403, "access forbidden", "permission/API configuration"),
            (403, "quotaExceeded", "quota"),
            (503, "backend unavailable", "transient YouTube API"),
        ]
        for status, detail, expected in cases:
            with self.subTest(status=status, detail=detail), \
                 patch.dict(os.environ, CREDS, clear=True), \
                 patch("output.access.make_client", return_value=object()), \
                 patch("output.access.authenticated_channel", side_effect=FakeHttpError(status, detail)):
                with self.assertRaisesRegex(SystemExit, expected):
                    auth_preflight.run_preflight()

    def test_preflight_source_has_no_youtube_mutation_path(self):
        source = (BASE / "output/access.py").read_text(encoding="utf-8")
        for forbidden in ("videos().insert", "upload_new(", "execute_upload(", "state.create("):
            self.assertNotIn(forbidden, source)
        self.assertIn("authenticated_channel(make_client())", source)


if __name__ == "__main__":
    unittest.main()
