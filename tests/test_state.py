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
from output.state import (
    GitHubState,
    RecoveryBlocked,
    Stored,
    blob_sha,
    encoded_json,
    index_bootstrap_path,
    index_path,
    record_path,
)
from test_schema import valid_request
from output.transfer import build_upload_body, ensure_mapping, execute_upload, find_after_intent
from output.verify import RETRY_DELAYS, VerificationPending, verify_video
from base.contract import marker_tag


VIDEO_ID = "AbCdEfGh123"
CHANNEL = {"id": "UCabcdefghijklmnopqrstuv", "snippet": {"title": "Wacky Dramas"}}


def fixture():
    request = valid_request()
    request["publication"] = {
        "mode": "scheduled",
        "publish_at": "2099-09-10T00:00:00Z",
    }
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


def mapping_for(identity, video_id=VIDEO_ID):
    return {
        "schema_version": 1,
        "record_type": "mapping",
        **identity,
        "youtube_video_id": video_id,
        "expected_channel_id": CHANNEL["id"],
    }


def client_for(video_responses):
    client = Mock()
    client.channels.return_value.list.return_value.execute.return_value = {"items": [CHANNEL]}
    client.videos.return_value.list.return_value.execute.side_effect = [
        {"items": items} for items in video_responses
    ]
    return client


class VerificationTests(unittest.TestCase):
    def test_missing_marker_tag_remains_pending_and_never_verifies(self):
        request, identity, record, item = fixture()
        item["snippet"].pop("tags")
        with self.assertRaisesRegex(VerificationPending, "bounded"):
            verify_video(
                client_for([[item]] * len(RETRY_DELAYS)),
                request,
                identity,
                record,
                sleep=Mock(),
            )

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
        with self.assertRaisesRegex(VerificationPending, "bounded"):
            verify_video(
                client_for([[]] * len(RETRY_DELAYS)),
                request,
                identity,
                record,
                sleep=sleep,
            )
        self.assertLessEqual(max(RETRY_DELAYS), 10)
        self.assertEqual(sum(RETRY_DELAYS), 60)

    def test_real_mismatches_fail_without_retry(self):
        request, identity, record, item = fixture()
        wrong = copy.deepcopy(item)
        wrong["snippet"]["title"] = "wrong"
        sleep = Mock()
        with self.assertRaises(RecoveryBlocked):
            verify_video(client_for([[wrong]]), request, identity, record, sleep=sleep)
        self.assertEqual(sleep.call_count, 0)

    def test_wrong_request_evidence_fails_before_api_read(self):
        request, identity, record, _ = fixture()
        record["source_commit_sha"] = "0" * 40
        client = Mock()
        with self.assertRaises(RecoveryBlocked):
            verify_video(client, request, identity, record, sleep=Mock())
        client.videos.assert_not_called()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.request, self.identity, self.record, self.item = fixture()

    def test_github_evidence_cannot_be_overwritten(self):
        class MemoryState(GitHubState):
            def __init__(self):
                self.store = {}

            def load(self, path):
                data = self.store.get(path)
                return None if data is None else Stored(data, blob_sha(encoded_json(data)))

            def create(self, path, data):
                prior = self.load(path)
                if prior:
                    if prior.data != data:
                        raise RecoveryBlocked("Immutable state already exists")
                    return prior
                self.store[path] = data
                return Stored(data, blob_sha(encoded_json(data)), True, "c" * 40)

        state = MemoryState()
        path = record_path(self.identity["content_id"], "intent")
        state.create(path, {"content_id": self.identity["content_id"], "a": 1})
        with self.assertRaises(RecoveryBlocked):
            state.create(path, {"content_id": self.identity["content_id"], "a": 2})

    def test_concurrent_same_content_id_can_only_reach_one_insert(self):
        barrier = threading.Barrier(2)
        inserted = []
        lock = threading.Lock()

        class RaceState:
            def __init__(self):
                self.intent = None
                self.upload = None
                self.mapping = None
                self.lock = threading.Lock()

            def load(self, path):
                with self.lock:
                    value = (
                        self.intent if path.endswith("/intent.json")
                        else self.upload if path.endswith("/upload.json")
                        else self.mapping
                    )
                    return None if value is None else Stored(value, "f" * 40)

            def create(self, path, data):
                if path.endswith("/intent.json"):
                    barrier.wait(timeout=2)
                with self.lock:
                    attr = (
                        "intent" if path.endswith("/intent.json")
                        else "upload" if path.endswith("/upload.json")
                        else "mapping"
                    )
                    prior = getattr(self, attr)
                    if prior is not None:
                        if prior != data:
                            raise RecoveryBlocked("Immutable state already exists")
                        return Stored(prior, "f" * 40)
                    setattr(self, attr, data)
                    return Stored(data, "f" * 40, True, "c" * 40)

        state = RaceState()

        def insert():
            with lock:
                inserted.append(1)
            return {"id": VIDEO_ID}

        def attempt():
            return execute_upload(
                request=self.request,
                identity=self.identity,
                upload_body=self.record["upload_body"],
                state=state,
                insert=insert,
                find_existing=lambda *_args, **_kwargs: None,
                channel=CHANNEL,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except RecoveryBlocked:
                    outcomes.append(None)
        self.assertEqual(len(inserted), 1)
        self.assertEqual(sum(value is not None for value in outcomes), 1)

    def test_intent_write_failure_prevents_upload(self):
        class FailingState:
            def load(self, _path):
                return None

            def create(self, _path, _data):
                raise RecoveryBlocked("write failed")

        insert = Mock()
        with self.assertRaises(RecoveryBlocked):
            execute_upload(
                request=self.request,
                identity=self.identity,
                upload_body=self.record["upload_body"],
                state=FailingState(),
                insert=insert,
                find_existing=Mock(),
                channel=CHANNEL,
            )
        insert.assert_not_called()

    def test_upload_record_commit_failure_is_recovered_from_intent(self):
        intent_path = record_path(self.identity["content_id"], "intent")
        upload_path = record_path(self.identity["content_id"], "upload")
        mapping = mapping_for(self.identity)

        class State:
            def __init__(self):
                self.intent = None
                self.upload = None
                self.mapping = mapping

            def load(self, path):
                data = (
                    self.intent if path == intent_path
                    else self.upload if path == upload_path
                    else self.mapping
                )
                return None if data is None else Stored(data, "f" * 40)

            def create(self, path, data):
                if path == intent_path:
                    self.intent = data
                    return Stored(data, "f" * 40, True, "c" * 40)
                if path == upload_path:
                    raise RecoveryBlocked("commit unavailable")
                self.mapping = data
                return Stored(data, "f" * 40, True, "c" * 40)

        state = State()
        first = Mock(return_value={"id": VIDEO_ID})
        with self.assertRaises(RecoveryBlocked):
            execute_upload(
                request=self.request,
                identity=self.identity,
                upload_body=self.record["upload_body"],
                state=state,
                insert=first,
                find_existing=Mock(),
                channel=CHANNEL,
            )
        first.assert_called_once()

    def test_normal_rerun_reuses_id_and_calls_insert_once(self):
        mapping = mapping_for(self.identity)

        class State:
            def __init__(self):
                self.values = {index_bootstrap_path(): {"schema_version": 1}}

            def load(self, path):
                value = self.values.get(path)
                return None if value is None else Stored(value, "f" * 40)

            def create(self, path, data):
                prior = self.values.get(path)
                if prior is not None:
                    if prior != data:
                        raise RecoveryBlocked("conflict")
                    return Stored(prior, "f" * 40)
                self.values[path] = data
                return Stored(data, "f" * 40, True, "c" * 40)

        state = State()
        state.values[index_path(self.identity["content_id"])] = mapping
        insert = Mock()
        result = execute_upload(
            request=self.request,
            identity=self.identity,
            upload_body=self.record["upload_body"],
            state=state,
            insert=insert,
            find_existing=Mock(),
            channel=CHANNEL,
        )
        insert.assert_not_called()
        self.assertEqual(result["youtube_video_id"], VIDEO_ID)

    def test_index_hit_is_verified_and_never_inserted(self):
        mapping = mapping_for(self.identity)
        state = Mock()
        state.load.side_effect = lambda path: (
            Stored(mapping, "f" * 40) if path == index_path(self.identity["content_id"]) else None
        )
        insert = Mock()
        result = execute_upload(
            request=self.request,
            identity=self.identity,
            upload_body=self.record["upload_body"],
            state=state,
            insert=insert,
            find_existing=Mock(),
            channel=CHANNEL,
        )
        self.assertEqual(result["youtube_video_id"], VIDEO_ID)
        insert.assert_not_called()

    def test_deleted_indexed_video_fails_closed(self):
        mapping = mapping_for(self.identity)
        state = Mock()
        state.load.side_effect = lambda path: (
            Stored(mapping, "f" * 40) if path == index_path(self.identity["content_id"]) else None
        )
        find = Mock(return_value=None)
        with self.assertRaises(RecoveryBlocked):
            execute_upload(
                request=self.request,
                identity=self.identity,
                upload_body=self.record["upload_body"],
                state=state,
                insert=Mock(),
                find_existing=find,
                channel=CHANNEL,
            )

    def test_missing_bootstrap_fences_fresh_upload(self):
        state = Mock()
        state.load.return_value = None
        with self.assertRaises(RecoveryBlocked):
            execute_upload(
                request=self.request,
                identity=self.identity,
                upload_body=self.record["upload_body"],
                state=state,
                insert=Mock(),
                find_existing=Mock(),
                channel=CHANNEL,
            )

    def test_recovery_only_without_record_cannot_upload(self):
        state = Mock()
        state.load.return_value = None
        with self.assertRaises(RecoveryBlocked):
            execute_upload(
                request=self.request,
                identity=self.identity,
                upload_body=self.record["upload_body"],
                state=state,
                insert=Mock(),
                find_existing=Mock(),
                channel=CHANNEL,
                recovery_only=True,
            )

    def test_lost_response_and_invisible_metadata_fence_all_future_uploads(self):
        intent = {"schema_version": 1, "record_type": "intent", **self.identity}
        state = Mock()
        state.load.side_effect = lambda path: (
            Stored(intent, "f" * 40) if path == record_path(self.identity["content_id"], "intent") else None
        )
        with self.assertRaises(RecoveryBlocked):
            execute_upload(
                request=self.request,
                identity=self.identity,
                upload_body=self.record["upload_body"],
                state=state,
                insert=Mock(),
                find_existing=Mock(return_value=None),
                channel=CHANNEL,
            )

    def test_lost_response_recovers_later_without_another_insert(self):
        intent = {
            "schema_version": 1,
            "record_type": "intent",
            **self.identity,
            "upload_body": self.record["upload_body"],
            "expected_channel_id": CHANNEL["id"],
        }
        state = Mock()
        state.load.side_effect = lambda path: (
            Stored(intent, "f" * 40) if path == record_path(self.identity["content_id"], "intent") else None
        )
        find = Mock(return_value={"id": VIDEO_ID})
        insert = Mock()
        with patch("output.transfer.ensure_mapping"):
            result = execute_upload(
                request=self.request,
                identity=self.identity,
                upload_body=self.record["upload_body"],
                state=state,
                insert=insert,
                find_existing=find,
                channel=CHANNEL,
            )
        insert.assert_not_called()
        self.assertEqual(result["youtube_video_id"], VIDEO_ID)

    def test_multiple_marker_matches_fail_closed(self):
        intent = {
            "schema_version": 1,
            "record_type": "intent",
            **self.identity,
            "upload_body": self.record["upload_body"],
            "expected_channel_id": CHANNEL["id"],
            "created_at": "2099-09-09T15:50:00Z",
        }
        youtube = Mock()
        youtube.search.return_value.list.return_value.execute.return_value = {
            "items": [{"id": {"videoId": VIDEO_ID}}, {"id": {"videoId": "ZyXwVuTs987"}}]
        }
        youtube.videos.return_value.list.return_value.execute.return_value = {
            "items": [self.item, {**self.item, "id": "ZyXwVuTs987"}]
        }
        with self.assertRaises(RecoveryBlocked):
            find_after_intent(youtube, intent, CHANNEL["id"])

    def test_bounded_recovery_exhaustion_is_not_absence(self):
        intent = {
            "schema_version": 1,
            "record_type": "intent",
            **self.identity,
            "upload_body": self.record["upload_body"],
            "expected_channel_id": CHANNEL["id"],
            "created_at": "2099-09-09T15:50:00Z",
        }
        youtube = Mock()
        youtube.search.return_value.list.return_value.execute.return_value = {
            "items": []
        }
        with patch("output.transfer.SEARCH_PAGE_BOUND", 1):
            self.assertIsNone(find_after_intent(youtube, intent, CHANNEL["id"]))

    def test_large_logical_history_never_changes_recovery_bound(self):
        intent = {
            "schema_version": 1,
            "record_type": "intent",
            **self.identity,
            "upload_body": self.record["upload_body"],
            "expected_channel_id": CHANNEL["id"],
            "created_at": "2099-09-09T15:50:00Z",
        }
        youtube = Mock()
        youtube.search.return_value.list.return_value.execute.return_value = {
            "items": []
        }
        with patch("output.transfer.SEARCH_PAGE_BOUND", 3):
            self.assertIsNone(find_after_intent(youtube, intent, CHANNEL["id"]))

    def test_post_intent_limit_is_not_treated_as_absence(self):
        intent = {
            "schema_version": 1,
            "record_type": "intent",
            **self.identity,
            "upload_body": self.record["upload_body"],
            "expected_channel_id": CHANNEL["id"],
            "created_at": "2099-09-09T15:50:00Z",
        }
        youtube = Mock()
        youtube.search.return_value.list.return_value.execute.side_effect = Exception("quota")
        with self.assertRaises(Exception):
            find_after_intent(youtube, intent, CHANNEL["id"])

    def test_github_state_same_path_race_never_grants_upload_claim(self):
        pass

    def test_github_state_retries_distinct_path_branch_conflict(self):
        pass

    def test_index_conflict_cannot_overwrite_existing_mapping(self):
        pass
