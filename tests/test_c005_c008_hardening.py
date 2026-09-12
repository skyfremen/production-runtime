import inspect
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from errors import E_EXEC, E_PREPARE
from output import progress, verify
from transform import process
import core


class _Videos:
    def __init__(self, item):
        self.item = item

    def list(self, **_kwargs):
        return self

    def execute(self):
        return {"items": [self.item]}


class _YouTube:
    def __init__(self, item):
        self._videos = _Videos(item)

    def videos(self):
        return self._videos


class HardeningTests(unittest.TestCase):
    def test_progress_is_append_only_started_evidence_visible_to_recovery(self):
        source = inspect.getsource(progress.record_progress)
        self.assertIn('"state": "started"', source)
        self.assertIn('"progress_stage": stage', source)
        self.assertIn('START_PREFIX', source)
        self.assertEqual(
            progress.ALLOWED_STAGES,
            {"prepared", "unit_started", "unit_produced", "unit_finished", "aggregate_started"},
        )
        self.assertEqual(progress.PRODUCTION_WORKFLOWS, {"Run", "One"})

    def test_unexpected_exception_produces_non_retryable_execution_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            diagnostic = Path(tmp) / "diagnostic.json"
            classification = Path(tmp) / "classification.json"
            with patch.object(core, "DIAGNOSTIC", diagnostic), patch.object(
                core, "FAILURE_CLASSIFICATION", classification
            ), patch.object(core, "INTERNAL_LOG", Path(tmp) / "missing.log"):
                try:
                    raise TypeError("programming defect")
                except Exception as exc:
                    code = core.record_failure(exc, preparation=False)
            payload = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual(code, E_EXEC)
        self.assertEqual(payload["stage"], "execute")
        self.assertTrue(payload["execution_started"])
        self.assertFalse(payload["retryable"])

    def test_unexpected_preparation_exception_is_stage_aware(self):
        with tempfile.TemporaryDirectory() as tmp:
            diagnostic = Path(tmp) / "diagnostic.json"
            classification = Path(tmp) / "classification.json"
            with patch.object(core, "DIAGNOSTIC", diagnostic), patch.object(
                core, "FAILURE_CLASSIFICATION", classification
            ), patch.object(core, "INTERNAL_LOG", Path(tmp) / "missing.log"):
                try:
                    raise AttributeError("bad preparation state")
                except Exception as exc:
                    code = core.record_failure(exc, preparation=True)
            payload = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual(code, E_PREPARE)
        self.assertEqual(payload["stage"], "prepare")
        self.assertFalse(payload["execution_started"])
        self.assertFalse(payload["retryable"])

    def test_render_subprocess_timeout_fails_closed(self):
        with patch.dict(os.environ, {"RENDER_SUBPROCESS_TIMEOUT_SECONDS": "30"}), patch(
            "transform.process.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["ffmpeg"], 30),
        ):
            with self.assertRaisesRegex(RuntimeError, "timed out after 30s"):
                process.bounded_render_capture(["ffmpeg", "-version"])

    def _verification_fixture(self, tags):
        content_id = "wd-20990101T000000-marker-test-a1b2c3"
        marker = verify.marker_tag(content_id)
        publish_at = "2099-01-01T00:00:00Z"
        identity = {
            "content_id": content_id,
            "request_path": f"youtube-shorts-bot/content/requests/{content_id}.json",
            "request_blob_sha": "a" * 40,
            "source_commit_sha": "b" * 40,
        }
        request = {
            "publication": {"mode": "scheduled", "publish_at": publish_at},
        }
        snippet = {
            "title": "t",
            "description": "d",
            "categoryId": "24",
            "channelId": "channel",
            "tags": tags,
        }
        evidence = {
            **identity,
            "record_type": "upload",
            "association": "intent_marker",
            "youtube_video_id": "abcdefghijk",
            "expected_channel_id": "channel",
            "upload_body": {
                "snippet": {"title": "t", "description": "d", "categoryId": "24", "tags": [marker]},
                "status": {"privacyStatus": "private", "publishAt": publish_at},
            },
        }
        item = {
            "id": "abcdefghijk",
            "snippet": snippet,
            "status": {
                "uploadStatus": "processed",
                "privacyStatus": "private",
                "publishAt": publish_at,
            },
        }
        return identity, request, evidence, item, marker

    def test_missing_recovery_marker_never_verifies_successfully(self):
        identity, request, evidence, item, _marker = self._verification_fixture([])
        with patch.object(
            verify, "authenticated_channel", return_value={"id": "channel", "snippet": {}}
        ):
            with self.assertRaises(verify.VerificationPending):
                verify.verify_video(
                    _YouTube(item), request, identity, evidence, sleep=lambda _delay: None
                )

    def test_recovery_marker_is_required_for_success(self):
        identity, request, evidence, item, marker = self._verification_fixture([])
        item["snippet"]["tags"] = [marker]
        with patch.object(
            verify, "authenticated_channel", return_value={"id": "channel", "snippet": {}}
        ):
            result = verify.verify_video(
                _YouTube(item), request, identity, evidence, sleep=lambda _delay: None
            )
        self.assertTrue(result["passed"])
        self.assertEqual(result["observed_marker_tags"], [marker])
        self.assertEqual(
            result["association_method"],
            "immutable_github_upload_record+remote_marker",
        )


if __name__ == "__main__":
    unittest.main()
