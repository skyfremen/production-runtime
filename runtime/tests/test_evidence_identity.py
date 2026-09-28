import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from output import result as result_module
from output import state, transfer


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class FakeState:
    def __init__(self, stored=None):
        self.stored = stored
        self.created = []

    def load(self, path):
        return self.stored

    def create(self, path, data):
        self.created.append((path, data))
        return state.Stored(data, "a" * 40, created=True, commit="b" * 40)


class EvidenceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.content_id = "wd-" + "1" * 24
        self.execution_id = "ex-" + "2" * 24
        self.request_id = "rq-" + "3" * 24
        self.identity = {
            "execution_id": self.execution_id,
            "content_id": self.content_id,
            "request_id": self.request_id,
            "request_path": f"content/requests/{self.request_id}.json",
            "request_source_sha": "4" * 40,
            "request_blob_sha": "5" * 40,
            "item_blob_sha": "6" * 40,
        }

    def _request_fixture(self, root):
        data = {"content_id": self.content_id}
        path = Path("runtime/content/requests") / f"{self.content_id}.json"
        absolute = root / path
        absolute.parent.mkdir(parents=True)
        absolute.write_text(json.dumps(data), encoding="utf-8")
        identity = dict(self.identity, item_blob_sha=state.blob_sha(absolute.read_bytes()))
        return path, data, identity

    def test_identity_for_requires_canonical_json(self):
        with tempfile.TemporaryDirectory() as tmp, working_directory(tmp):
            path, data, _identity = self._request_fixture(Path(tmp))
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(state.RecoveryBlocked, "canonical identity"):
                    state.identity_for(path, data)
            with patch.dict(os.environ, {"REQUEST_IDENTITY_JSON": "{"}, clear=True):
                with self.assertRaisesRegex(state.RecoveryBlocked, "canonical identity"):
                    state.identity_for(path, data)

    def test_identity_for_returns_batch_identity_and_checks_item_blob(self):
        with tempfile.TemporaryDirectory() as tmp, working_directory(tmp):
            path, data, identity = self._request_fixture(Path(tmp))
            with patch.dict(
                os.environ,
                {"REQUEST_IDENTITY_JSON": json.dumps(identity)},
                clear=True,
            ):
                self.assertEqual(identity, state.identity_for(path, data))
                bad = dict(identity, item_blob_sha="7" * 40)
                os.environ["REQUEST_IDENTITY_JSON"] = json.dumps(bad)
                with self.assertRaisesRegex(state.RecoveryBlocked, "immutable source"):
                    state.identity_for(path, data)

    def test_check_identity_accepts_exact_v2(self):
        state.check_identity({"evidence_version": 2, **self.identity}, self.identity)
        for field in self.identity:
            evidence = {"evidence_version": 2, **self.identity, field: "wrong"}
            with self.subTest(field=field), self.assertRaises(state.RecoveryBlocked):
                state.check_identity(evidence, self.identity)

    def test_check_identity_accepts_only_exact_legacy_alias(self):
        legacy = {
            "evidence_version": 1,
            "content_id": self.content_id,
            "request_path": f"content/requests/{self.content_id}.json",
            "request_blob_sha": self.identity["item_blob_sha"],
            "source_commit_sha": self.identity["request_source_sha"],
        }
        state.check_identity(legacy, self.identity)
        for field in ("content_id", "request_path", "request_blob_sha", "source_commit_sha"):
            evidence = dict(legacy, **{field: "wrong"})
            with self.subTest(field=field), self.assertRaises(state.RecoveryBlocked):
                state.check_identity(evidence, self.identity)

    def test_check_identity_rejects_unknown_version(self):
        with self.assertRaisesRegex(state.RecoveryBlocked, "version"):
            state.check_identity({"evidence_version": 3, **self.identity}, self.identity)

    def test_new_upload_record_is_v2_and_preserves_identity(self):
        intent = {"upload_body": {"status": {"privacyStatus": "private"}}}
        with patch.object(transfer, "now", return_value="2026-09-27T00:00:00Z"), patch.object(
            transfer, "workflow_identity", return_value={"run_id": "1"}
        ):
            record = transfer.upload_record(
                self.identity, intent, "abcdefghijk", "channel", "videos.insert"
            )
        self.assertEqual(2, record["evidence_version"])
        for field, value in self.identity.items():
            self.assertEqual(value, record[field])

    def test_new_intent_is_v2_and_preserves_identity(self):
        request = {
            "content_id": self.content_id,
            "visibility": "private",
            "publication": {"mode": "scheduled", "publish_at": "2026-10-01T00:00:00Z"},
            "youtube": {
                "title": "Title",
                "description": "Description",
                "hashtags": [],
                "tags": [],
                "category_id": "24",
            },
        }
        durable = FakeState()
        with patch.object(
            transfer, "authenticated_channel", return_value={"id": "channel"}
        ), patch.object(
            transfer, "workflow_identity", return_value={"run_id": "1"}
        ), patch.object(
            transfer, "reconcile_intent", side_effect=state.RecoveryBlocked("stop")
        ), self.assertRaisesRegex(state.RecoveryBlocked, "stop"):
            transfer.upload_new(
                durable, "request.json", request, self.identity, "video.mp4", MagicMock()
            )
        intent = durable.created[0][1]
        self.assertEqual(2, intent["evidence_version"])
        for field, value in self.identity.items():
            self.assertEqual(value, intent[field])

    def test_matching_v1_upload_prevents_duplicate_and_remains_unchanged(self):
        legacy = {
            "evidence_version": 1,
            "content_id": self.content_id,
            "request_path": f"content/requests/{self.content_id}.json",
            "request_blob_sha": self.identity["item_blob_sha"],
            "source_commit_sha": self.identity["request_source_sha"],
            "youtube_video_id": "abcdefghijk",
        }
        durable = FakeState(state.Stored(legacy, "a" * 40))
        youtube = MagicMock()
        with patch.object(
            transfer, "authenticated_channel", return_value={"id": "channel"}
        ):
            result = transfer.prepare_upload(
                durable, "request.json", {}, self.identity, youtube
            )
        self.assertFalse(result["upload_required"])
        self.assertIs(legacy, result["upload_evidence"])
        self.assertEqual([], durable.created)
        youtube.videos.return_value.insert.assert_not_called()

    def test_mismatched_v1_upload_blocks_before_insert(self):
        legacy = {
            "evidence_version": 1,
            "content_id": self.content_id,
            "request_path": f"content/requests/{self.content_id}.json",
            "request_blob_sha": "0" * 40,
            "source_commit_sha": self.identity["request_source_sha"],
        }
        durable = FakeState(state.Stored(legacy, "a" * 40))
        youtube = MagicMock()
        with patch.object(
            transfer, "authenticated_channel", return_value={"id": "channel"}
        ), self.assertRaises(state.RecoveryBlocked):
            transfer.upload_new(
                durable, "request.json", {}, self.identity, "video.mp4", youtube
            )
        youtube.videos.return_value.insert.assert_not_called()

    def test_result_rejects_verification_for_another_identity(self):
        verification = {
            "passed": True,
            "privacy_status": "private",
            "publish_at": "2026-10-01T00:00:00Z",
            "youtube_video_id": "abcdefghijk",
            **dict(self.identity, content_id="wd-" + "9" * 24),
        }
        durable = MagicMock()
        with patch.object(result_module, "identity_for", return_value=self.identity), patch.object(
            result_module, "load_json", side_effect=[{"content_id": self.content_id}, verification]
        ), patch.object(result_module, "GitHubState", return_value=durable), patch.dict(
            os.environ, {"EXECUTION_ID": self.execution_id}, clear=True
        ), patch(
            "sys.argv", ["result.py", "--request", "request.json"]
        ), self.assertRaises(state.RecoveryBlocked):
            result_module.main()
        durable.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
