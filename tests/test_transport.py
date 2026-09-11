import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from transport import (
    PrivateState,
    TransportError,
    git_blob_sha,
    record_start,
    refresh_registry,
)


class TransportConcurrencyTests(unittest.TestCase):
    def test_immutable_create_retries_unrelated_branch_conflict(self):
        state = object.__new__(PrivateState)
        state.current_content = Mock(side_effect=[
            TransportError("missing"), TransportError("missing"), TransportError("missing")
        ])
        state.api = Mock(side_effect=[TransportError("conflict"), {}])
        with patch("transport.time.sleep"):
            created = state.create("opaque/result.json", {"value": 1}, "message")
        self.assertTrue(created)
        self.assertEqual(state.api.call_count, 2)

    def test_refresh_requires_exact_prepared_registry_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            registry.write_text("old")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"registry": str(registry)}))
            raw = b'{"schema_version":3}\n'
            sha = git_blob_sha(raw)
            state = Mock()
            state.current_content.return_value = (raw, sha)
            with patch("transport.PrivateState", return_value=state):
                refresh_registry(manifest, sha)
            self.assertEqual(registry.read_bytes(), raw)
            with patch("transport.PrivateState", return_value=state):
                with self.assertRaises(TransportError):
                    refresh_registry(manifest, "a" * 40)


class StartEvidenceTests(unittest.TestCase):
    BATCH = "b_" + "a" * 30
    SOURCE = "b" * 40
    CONTRACT = "c" * 64
    DISPATCH = "d_" + "d" * 24

    def intent(self, **changes):
        value = {
            "schema_version": 1,
            "state": "prepared",
            "batch_id": self.BATCH,
            "dispatch_id": self.DISPATCH,
            "source_sha": self.SOURCE,
            "contract_hash": self.CONTRACT,
        }
        value.update(changes)
        return value

    def environment(self):
        return {
            "PRIVATE_STATE_REPOSITORY": "owner/private-state",
            "PRIVATE_STATE_TOKEN": "secret",
            "RUNTIME_COMMIT_SHA": "e" * 40,
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "2",
        }

    def test_start_is_tied_to_exact_prepared_dispatch(self):
        state = Mock()
        state.current_content.return_value = (
            json.dumps(self.intent()).encode(),
            "unused",
        )
        with patch.dict(os.environ, self.environment(), clear=False), patch(
            "transport.PrivateState", return_value=state
        ):
            record_start(self.BATCH, self.SOURCE, self.CONTRACT, self.DISPATCH)

        state.create.assert_called_once()
        path, payload, message = state.create.call_args.args
        self.assertEqual(
            path,
            f"youtube-shorts-bot/content/recovery/starts/{self.BATCH}/{self.DISPATCH}/12345-2.json",
        )
        self.assertEqual(payload["state"], "started")
        self.assertEqual(payload["batch_id"], self.BATCH)
        self.assertEqual(payload["dispatch_id"], self.DISPATCH)
        self.assertEqual(payload["source_sha"], self.SOURCE)
        self.assertEqual(payload["contract_hash"], self.CONTRACT)
        self.assertEqual(payload["runtime_commit_sha"], "e" * 40)
        self.assertEqual(payload["workflow_run_id"], "12345")
        self.assertEqual(payload["workflow_run_attempt"], "2")
        self.assertTrue(payload["started_at"].endswith("Z"))
        self.assertEqual(message, "record opaque runtime start")

    def test_stale_or_mismatched_dispatch_cannot_start(self):
        state = Mock()
        state.current_content.return_value = (
            json.dumps(self.intent(contract_hash="f" * 64)).encode(),
            "unused",
        )
        with patch.dict(os.environ, self.environment(), clear=False), patch(
            "transport.PrivateState", return_value=state
        ):
            with self.assertRaises(TransportError):
                record_start(self.BATCH, self.SOURCE, self.CONTRACT, self.DISPATCH)
        state.create.assert_not_called()

    def test_start_write_failure_is_fail_closed(self):
        state = Mock()
        state.current_content.return_value = (
            json.dumps(self.intent()).encode(),
            "unused",
        )
        state.create.side_effect = TransportError("write failed")
        with patch.dict(os.environ, self.environment(), clear=False), patch(
            "transport.PrivateState", return_value=state
        ):
            with self.assertRaises(TransportError):
                record_start(self.BATCH, self.SOURCE, self.CONTRACT, self.DISPATCH)


if __name__ == "__main__":
    unittest.main()
