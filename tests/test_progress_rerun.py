import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from output import progress
from transport import TransportError


class FakeState:
    def __init__(self, batch_id, dispatch_id, files=None, race_start_path=None, race_payload=None):
        self.batch_id = batch_id
        self.dispatch_id = dispatch_id
        self.files = dict(files or {})
        self.race_start_path = race_start_path
        self.race_payload = race_payload

    def api(self, endpoint, *, method="GET", body=None):
        expected = f"contents/youtube-shorts-bot/content/recovery/starts/{self.batch_id}?ref=main"
        if endpoint == expected and method == "GET":
            return [{"name": self.dispatch_id, "type": "dir"}]
        raise TransportError("unexpected api call")

    def current_content(self, path):
        if path not in self.files:
            raise TransportError("missing")
        raw = self.files[path]
        return raw, "unused"

    def create(self, path, payload, message):
        if self.race_start_path == path:
            self.files[path] = (json.dumps(self.race_payload, indent=2, sort_keys=True) + "\n").encode()
            self.race_start_path = None
            raise TransportError("simulated concurrent create")
        raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        if path in self.files and self.files[path] != raw:
            raise TransportError("immutable differs")
        self.files[path] = raw
        return True


class ProgressRerunStartTests(unittest.TestCase):
    BATCH = "r_" + "a" * 30
    SOURCE = "b" * 40
    CONTRACT = "c" * 64
    DISPATCH = "d_" + "d" * 24
    RUNTIME = "e" * 40
    RUN_ID = "12345"

    def env(self, attempt):
        return {
            "GITHUB_WORKFLOW": "Run",
            "GITHUB_RUN_ID": self.RUN_ID,
            "GITHUB_RUN_ATTEMPT": str(attempt),
            "RUNTIME_COMMIT_SHA": self.RUNTIME,
        }

    def intent(self, **changes):
        payload = {
            "schema_version": 1,
            "state": "prepared",
            "batch_id": self.BATCH,
            "dispatch_id": self.DISPATCH,
            "source_sha": self.SOURCE,
            "contract_hash": self.CONTRACT,
        }
        payload.update(changes)
        return payload

    def start(self, attempt, started_at="2026-09-12T16:00:00Z", **changes):
        payload = {
            "schema_version": 1,
            "state": "started",
            "batch_id": self.BATCH,
            "dispatch_id": self.DISPATCH,
            "source_sha": self.SOURCE,
            "contract_hash": self.CONTRACT,
            "runtime_commit_sha": self.RUNTIME,
            "workflow_run_id": self.RUN_ID,
            "workflow_run_attempt": str(attempt),
            "started_at": started_at,
        }
        payload.update(changes)
        return payload

    @staticmethod
    def raw(payload):
        return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()

    def paths(self, attempt):
        root = f"youtube-shorts-bot/content/recovery"
        return {
            "intent": f"{root}/dispatch-intents/{self.BATCH}/{self.DISPATCH}.json",
            "start": f"{root}/starts/{self.BATCH}/{self.DISPATCH}/{self.RUN_ID}-{attempt}.json",
            "progress": f"{root}/starts/{self.BATCH}/{self.DISPATCH}/{self.RUN_ID}-{attempt}-unit_started-00.json",
        }

    def test_partial_rerun_synthesizes_current_attempt_start_before_progress(self):
        prior = self.paths(1)
        current = self.paths(2)
        state = FakeState(
            self.BATCH,
            self.DISPATCH,
            {
                prior["intent"]: self.raw(self.intent()),
                prior["start"]: self.raw(self.start(1)),
            },
        )
        with patch.dict(os.environ, self.env(2), clear=False), patch(
            "output.progress.PrivateState", return_value=state
        ):
            progress.record_progress(
                {"batch_id": self.BATCH, "source_sha": self.SOURCE},
                "unit_started",
                0,
            )

        self.assertIn(current["start"], state.files)
        self.assertIn(current["progress"], state.files)
        synthesized = json.loads(state.files[current["start"]])
        self.assertEqual(synthesized["workflow_run_attempt"], "2")
        self.assertEqual(synthesized["contract_hash"], self.CONTRACT)
        self.assertTrue(synthesized["started_at"].endswith("Z"))

    def test_rerun_requires_prior_start_for_same_run_and_runtime(self):
        paths = self.paths(1)
        state = FakeState(
            self.BATCH,
            self.DISPATCH,
            {paths["intent"]: self.raw(self.intent())},
        )
        with patch.dict(os.environ, self.env(2), clear=False), patch(
            "output.progress.PrivateState", return_value=state
        ):
            with self.assertRaises(TransportError):
                progress.record_progress(
                    {"batch_id": self.BATCH, "source_sha": self.SOURCE},
                    "unit_started",
                    0,
                )

    def test_rerun_revalidates_immutable_dispatch_intent(self):
        paths = self.paths(1)
        state = FakeState(
            self.BATCH,
            self.DISPATCH,
            {
                paths["intent"]: self.raw(self.intent(contract_hash="f" * 64)),
                paths["start"]: self.raw(self.start(1)),
            },
        )
        with patch.dict(os.environ, self.env(2), clear=False), patch(
            "output.progress.PrivateState", return_value=state
        ):
            with self.assertRaises(TransportError):
                progress.record_progress(
                    {"batch_id": self.BATCH, "source_sha": self.SOURCE},
                    "unit_started",
                    0,
                )

    def test_concurrent_matrix_race_accepts_only_equivalent_current_start(self):
        prior = self.paths(1)
        current = self.paths(2)
        winner = self.start(2, started_at="2026-09-12T17:00:01Z")
        state = FakeState(
            self.BATCH,
            self.DISPATCH,
            {
                prior["intent"]: self.raw(self.intent()),
                prior["start"]: self.raw(self.start(1)),
            },
            race_start_path=current["start"],
            race_payload=winner,
        )
        with patch.dict(os.environ, self.env(2), clear=False), patch(
            "output.progress.PrivateState", return_value=state
        ):
            progress.record_progress(
                {"batch_id": self.BATCH, "source_sha": self.SOURCE},
                "unit_started",
                0,
            )

        self.assertEqual(json.loads(state.files[current["start"]]), winner)
        self.assertIn(current["progress"], state.files)

    def test_first_attempt_still_requires_explicit_entry_start(self):
        paths = self.paths(1)
        state = FakeState(
            self.BATCH,
            self.DISPATCH,
            {paths["intent"]: self.raw(self.intent())},
        )
        with patch.dict(os.environ, self.env(1), clear=False), patch(
            "output.progress.PrivateState", return_value=state
        ):
            with self.assertRaises(TransportError):
                progress.record_progress(
                    {"batch_id": self.BATCH, "source_sha": self.SOURCE},
                    "unit_started",
                    0,
                )


if __name__ == "__main__":
    unittest.main()
