import copy
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from output.execute import prepare
from output.state import GitHubState, RecoveryBlocked, Stored, blob_sha, encoded_json, record_path
from test_schema import valid_request
from output.transfer import build_upload_body, execute_upload, find_existing_by_marker
from output.verify import RETRY_DELAYS, verify_video
from base.contract import marker_tag

VIDEO_ID = "AbCdEfGh123"
CHANNEL = {
    "id": "UCvrq2m9G4yrwPfL_X-QPzMA",
    "snippet": {"title": "Wa" + "cky" + " Dramas"},
    "contentDetails": {"relatedPlaylists": {"uploads": "uploads"}},
}


class MemoryState:
    def __init__(self):
        self.records = {}
        self.writes = []

    def load(self, path):
        if path in self.records:
            data = self.records[path]
            return Stored(copy.deepcopy(data), blob_sha(encoded_json(data)))
        return None

    def create(self, path, data):
        prior = self.load(path)
        if prior:
            if prior.data != data:
                raise RecoveryBlocked("immutable")
            return prior
        self.records[path] = copy.deepcopy(data)
        self.writes.append(path)
        return Stored(copy.deepcopy(data), blob_sha(encoded_json(data)), True, "c" * 40)


class AtomicMemoryState(MemoryState):
    def __init__(self):
        super().__init__()
        self.lock = threading.RLock()

    def load(self, path):
        with self.lock:
            return super().load(path)

    def create(self, path, data):
        with self.lock:
            return super().create(path, data)


def fixture():
    request = valid_request()
    identity = {
        "content_id": request["content_id"],
        "request_path": (
            "runtime/content/requests/" + request["content_id"] + ".json"
        ),
        "request_blob_sha": "a" * 40,
        "source_commit_sha": "b" * 40,
    }
    upload_body = build_upload_body(request, require_future=False)
    record = {
        "schema_version": 1,
        "record_type": "upload",
        **identity,
        "youtube_video_id": VIDEO_ID,
        "expected_channel_id": CHANNEL["id"],
        "created_at": "2099-09-09T15:50:00Z",
        "uploaded_at": "2099-09-09T15:51:00Z",
        "background": {},
        "render": {},
        "upload_workflow": {
            "name": "Daily Production",
            "run_id": "1",
            "run_attempt": "1",
            "code_commit_sha": "b" * 40,
        },
        "upload_body": upload_body,
        "association": {"kind": "youtube_insert_response", "response": {"id": VIDEO_ID}},
    }
    item = {
        "id": VIDEO_ID,
        "snippet": {
            **upload_body["snippet"],
            "channelId": CHANNEL["id"],
            "publishedAt": "2099-09-09T15:51:00Z",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": request["publication"]["publish_at"],
            "uploadStatus": "processed",
        },
    }
    return request, identity, record, item


def client_for(video_responses):
    client = Mock()
    client.channels.return_value.list.return_value.execute.return_value = {"items": [CHANNEL]}
    client.videos.return_value.list.return_value.execute.side_effect = [
        {"items": items} for items in video_responses
    ]
    return client


class VerificationTests(unittest.TestCase):
    def test_missing_marker_tag_does_not_break_durable_video_id_verification(self):
        request, identity, record, item = fixture()
        item["snippet"].pop("tags")
        result = verify_video(
            client_for([[item]]), request, identity, record, sleep=Mock()
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["state"], "verified_scheduled")
        self.assertEqual(result["observed_marker_tags"], [])

    def test_current_marker_is_observed(self):
        request, identity, record, item = fixture()
        result = verify_video(
            client_for([[item]]), request, identity, record, sleep=Mock()
        )
        self.assertEqual(
            result["observed_marker_tags"], [marker_tag(identity["content_id"])]
        )

    def test_not_visible_then_processing_then_ready(self):
        request, identity, record, item = fixture()
        pending = copy.deepcopy(item)
        pending["status"]["uploadStatus"] = "uploaded"
        sleep = Mock()
        result = verify_video(
            client_for([[], [pending], [item]]),
            request,
            identity,
            record,
            sleep=sleep,
        )
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(result["state"], "verified_scheduled")

    def test_read_delay_is_bounded(self):
        request, identity, record, _ = fixture()
        sleep = Mock()
        with self.assertRaisesRegex(RecoveryBlocked, "bounded"):
            verify_video(
                client_for([[] for _ in RETRY_DELAYS]),
                request,
                identity,
                record,
                sleep=sleep,
            )
        self.assertEqual(sleep.call_count, len(RETRY_DELAYS) - 1)

    def test_real_mismatches_fail_without_retry(self):
        cases = [
            ("privacy", "unlisted"),
            ("publishAt", None),
            ("publishAt", ""),
            ("publishAt", "2099-01-01T00:00:00Z"),
            ("id", "another0000"),
            ("owner", "foreign"),
            ("title", "Other story"),
            ("description", "Changed"),
            ("uploadStatus", "failed"),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                request, identity, record, item = fixture()
                if field == "privacy":
                    item["status"]["privacyStatus"] = value
                elif field == "publishAt":
                    item["status"][field] = value
                elif field == "id":
                    item["id"] = value
                elif field == "owner":
                    item["snippet"]["channelId"] = value
                elif field in {"title", "description"}:
                    item["snippet"][field] = value
                else:
                    item["status"][field] = value
                sleep = Mock()
                with self.assertRaises(RecoveryBlocked):
                    verify_video(
                        client_for([[item]]),
                        request,
                        identity,
                        record,
                        sleep=sleep,
                    )
                sleep.assert_not_called()

    def test_wrong_request_evidence_fails_before_api_read(self):
        request, identity, record, _ = fixture()
        record["request_blob_sha"] = "f" * 40
        client = Mock()
        with self.assertRaises(RecoveryBlocked):
            verify_video(client, request, identity, record)
        client.channels.assert_not_called()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for module in ("output.transfer", "output.execute"):
            output_patch = patch(module + ".OUTPUT_DIR", Path(self.temp.name))
            output_patch.start()
            self.addCleanup(output_patch.stop)
        workflow_patch = patch(
            "output.transfer.workflow_identity",
            return_value={
                "name": "Daily Production",
                "run_id": "2",
                "run_attempt": "1",
                "code_commit_sha": "b" * 40,
            },
        )
        workflow_patch.start()
        self.addCleanup(workflow_patch.stop)
        self.request, self.identity, self.record, self.item = fixture()
        self.state = MemoryState()

    def execute(self):
        return execute_upload(
            self.request,
            Path(self.temp.name) / "video.mp4",
            state=self.state,
            identity=self.identity,
            channel=CHANNEL,
            selection={},
            render_meta={},
            youtube=Mock(),
        )

    @patch("output.transfer.find_existing_by_marker", return_value=None)
    @patch("output.transfer.upload_new", return_value={"id": VIDEO_ID})
    def test_normal_rerun_reuses_id_and_calls_insert_once(self, insert, _lookup):
        first, recovered = self.execute()
        second, recovered_again = self.execute()
        self.assertFalse(recovered)
        self.assertTrue(recovered_again)
        self.assertEqual(first.data["youtube_video_id"], second.data["youtube_video_id"])
        self.assertEqual(first.data["upload_body"]["status"]["privacyStatus"], "private")
        self.assertEqual(
            first.data["upload_body"]["status"]["publishAt"],
            self.request["publication"]["publish_at"],
        )
        insert.assert_called_once()
        self.assertEqual(len(self.state.writes), 2)

    @patch("output.transfer.find_existing_by_marker", return_value=None)
    def test_concurrent_same_content_id_can_only_reach_one_insert(self, _lookup):
        self.state = AtomicMemoryState()
        insert_started = threading.Event()
        release_insert = threading.Event()
        insert_calls = []

        def slow_insert(*_args, **_kwargs):
            insert_calls.append(1)
            insert_started.set()
            release_insert.wait(timeout=2)
            return {"id": VIDEO_ID}

        with patch("output.transfer.upload_new", side_effect=slow_insert):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(self.execute)
                self.assertTrue(insert_started.wait(timeout=2))
                second = executor.submit(self.execute)
                with self.assertRaises(RecoveryBlocked):
                    second.result(timeout=2)
                release_insert.set()
                stored, recovered = first.result(timeout=2)

        self.assertFalse(recovered)
        self.assertEqual(stored.data["youtube_video_id"], VIDEO_ID)
        self.assertEqual(len(insert_calls), 1)

    @patch("output.transfer.find_existing_by_marker", return_value=None)
    @patch("output.transfer.upload_new", side_effect=TimeoutError("response lost"))
    def test_lost_response_and_invisible_metadata_fence_all_future_uploads(
        self, insert, _lookup
    ):
        with self.assertRaises(RecoveryBlocked):
            self.execute()
        with self.assertRaises(RecoveryBlocked):
            self.execute()
        insert.assert_called_once()
        self.assertIn(record_path(self.identity["content_id"], "intent"), self.state.records)

    @patch("output.transfer.upload_new", side_effect=TimeoutError("response lost"))
    def test_lost_response_recovers_later_without_another_insert(self, insert):
        with patch("output.transfer.find_existing_by_marker", return_value=None):
            with self.assertRaises(RecoveryBlocked):
                self.execute()
        with patch("output.transfer.find_existing_by_marker", return_value=self.item):
            stored, recovered = self.execute()
        self.assertTrue(recovered)
        self.assertEqual(stored.data["youtube_video_id"], VIDEO_ID)
        insert.assert_called_once()

    @patch("output.transfer.find_existing_by_marker", return_value=None)
    @patch("output.transfer.upload_new")
    def test_intent_write_failure_prevents_upload(self, insert, _lookup):
        self.state.create = Mock(side_effect=RecoveryBlocked("GitHub unavailable"))
        with self.assertRaises(RecoveryBlocked):
            self.execute()
        insert.assert_not_called()

    @patch("output.transfer.find_existing_by_marker", return_value=None)
    @patch("output.transfer.upload_new", return_value={"id": VIDEO_ID})
    def test_upload_record_commit_failure_is_recovered_from_intent(self, insert, lookup):
        real_create = self.state.create

        def fail_upload(path, data):
            if path.endswith("/upload.json"):
                raise RecoveryBlocked("write response lost")
            return real_create(path, data)

        self.state.create = fail_upload
        with self.assertRaises(RecoveryBlocked):
            self.execute()
        self.state.create = real_create
        lookup.return_value = self.item
        stored, recovered = self.execute()
        self.assertTrue(recovered)
        self.assertEqual(stored.data["youtube_video_id"], VIDEO_ID)
        insert.assert_called_once()

    @patch("output.transfer.upload_new")
    def test_recovery_only_without_record_cannot_upload(self, insert):
        with self.assertRaisesRegex(RecoveryBlocked, "Recovery-only"):
            prepare(self.request, self.identity, self.state, Mock(), CHANNEL, True)
        insert.assert_not_called()

    def test_multiple_marker_matches_fail_closed(self):
        other = copy.deepcopy(self.item)
        other["id"] = "another0000"
        client = client_for([[self.item, other]])
        client.playlistItems.return_value.list.return_value.execute.return_value = {
            "items": [
                {"contentDetails": {"videoId": VIDEO_ID}},
                {"contentDetails": {"videoId": "another0000"}},
            ]
        }
        with self.assertRaisesRegex(RecoveryBlocked, "Multiple"):
            find_existing_by_marker(client, self.identity["content_id"], channel=CHANNEL)

    def test_inventory_truncation_is_not_absence(self):
        client = Mock()
        client.playlistItems.return_value.list.return_value.execute.return_value = {
            "items": [{"contentDetails": {"videoId": VIDEO_ID}}],
            "nextPageToken": "more",
        }
        with self.assertRaisesRegex(RecoveryBlocked, "inventory limit"):
            find_existing_by_marker(
                client, self.identity["content_id"], max_videos=1, channel=CHANNEL
            )

    def test_github_evidence_cannot_be_overwritten(self):
        state = object.__new__(GitHubState)
        state.load = Mock(return_value=Stored(self.record, "a" * 40))
        state.api = Mock()
        changed = copy.deepcopy(self.record)
        changed["youtube_video_id"] = "other000000"
        with self.assertRaisesRegex(RecoveryBlocked, "Immutable"):
            state.create(record_path(self.identity["content_id"], "upload"), changed)
        state.api.assert_not_called()

    def test_github_state_retries_distinct_path_branch_conflict(self):
        state = object.__new__(GitHubState)
        state.load = Mock(side_effect=[None, None])
        raw = encoded_json(self.record)
        acknowledged = {
            "content": {"sha": blob_sha(raw)},
            "commit": {"sha": "b" * 40},
        }
        state.api = Mock(side_effect=[
            HTTPError("https://example.invalid", 409, "conflict", {}, None),
            acknowledged,
        ])
        with patch("output.state.time.sleep"):
            stored = state.create(
                record_path(self.identity["content_id"], "upload"), self.record
            )
        self.assertTrue(stored.created)
        self.assertEqual(state.api.call_count, 2)

    def test_github_state_same_path_race_never_grants_upload_claim(self):
        state = object.__new__(GitHubState)
        prior = Stored(self.record, "a" * 40)
        state.load = Mock(side_effect=[None, prior])
        state.api = Mock(side_effect=HTTPError(
            "https://example.invalid", 422, "exists", {}, None
        ))
        stored = state.create(
            record_path(self.identity["content_id"], "upload"), self.record
        )
        self.assertFalse(stored.created)

    def test_github_state_cannot_write_requests_or_code(self):
        for path in (
            "runtime/transform/compose.py",
            self.identity["request_path"],
            ".github/workflows/example.yml",
        ):
            with self.assertRaises(RecoveryBlocked):
                GitHubState.allowed(path)


if __name__ == "__main__":
    unittest.main()
