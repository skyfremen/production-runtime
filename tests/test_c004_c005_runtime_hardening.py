import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from engine.batch import BatchError, resolve_manual_batch
from engine.pipeline import PipelineError, ProductionPipeline


class WorkerTimeoutTests(unittest.TestCase):
    def test_run_command_passes_configured_timeout_to_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "child.log"
            env = {"RUNTIME_CHILD_TIMEOUT_SECONDS": "75"}
            with patch(
                "engine.pipeline.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ) as run:
                ProductionPipeline.run_command(
                    ["python", "runtime/worker.py"], env, log_path
                )
        self.assertEqual(run.call_args.kwargs["timeout"], 75)

    def test_run_command_uses_existing_1200_second_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "child.log"
            with patch(
                "engine.pipeline.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ) as run:
                ProductionPipeline.run_command(
                    ["python", "runtime/worker.py"], {}, log_path
                )
        self.assertEqual(run.call_args.kwargs["timeout"], 1200)

    def test_timeout_is_translated_to_pipeline_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "child.log"
            env = {"RUNTIME_CHILD_TIMEOUT_SECONDS": "60"}
            with patch(
                "engine.pipeline.subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    cmd=["python", "runtime/worker.py"], timeout=60
                ),
            ):
                with self.assertRaisesRegex(
                    PipelineError, r"worker\.py timed out after 60s"
                ):
                    ProductionPipeline.run_command(
                        ["python", "runtime/worker.py"], env, log_path
                    )

    def test_invalid_timeout_fails_before_child_starts(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "child.log"
            with patch("engine.pipeline.subprocess.run") as run:
                with self.assertRaisesRegex(PipelineError, "between 60 and 3600"):
                    ProductionPipeline.run_command(
                        ["python", "runtime/worker.py"],
                        {"RUNTIME_CHILD_TIMEOUT_SECONDS": "59"},
                        log_path,
                    )
            run.assert_not_called()


class ManualBatchFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.previous_cwd)
        self.requests = self.root / "runtime" / "content" / "requests"
        self.sourcing = self.root / "runtime" / "content" / "background-sourcing"
        self.requests.mkdir(parents=True)
        self.sourcing.mkdir(parents=True)
        self.content_id = "wd-20990910T000000-manual-test-a1b2c3"
        self.request_path = self.requests / f"{self.content_id}.json"

    def immutable_source(self, *_args, **_kwargs):
        return "a" * 40 + "\n"

    def write_valid_request(self):
        self.request_path.write_text(
            json.dumps(
                {
                    "visual": {
                        "background_primary_id": "background-1",
                        "background_backup_id": "background-2",
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_malformed_selected_request_is_not_silently_ignored(self):
        self.request_path.write_text("{ definitely not json", encoding="utf-8")
        with patch("engine.batch._run_git", side_effect=self.immutable_source):
            with self.assertRaisesRegex(
                BatchError, "Cannot read selected manual request"
            ):
                resolve_manual_batch(self.content_id, cwd=self.root)

    def test_malformed_sourcing_manifest_is_not_silently_ignored(self):
        self.write_valid_request()
        (self.sourcing / "bad.json").write_text(
            "{ definitely not json", encoding="utf-8"
        )
        with patch("engine.batch._run_git", side_effect=self.immutable_source):
            with self.assertRaisesRegex(
                BatchError, "Cannot read background sourcing manifest"
            ):
                resolve_manual_batch(self.content_id, cwd=self.root)

    def test_invalid_sourcing_candidate_shape_fails_closed(self):
        self.write_valid_request()
        (self.sourcing / "bad.json").write_text(
            json.dumps({"candidates": ["not-an-object"]}), encoding="utf-8"
        )
        with patch("engine.batch._run_git", side_effect=self.immutable_source):
            with self.assertRaisesRegex(
                BatchError, "Background sourcing candidate 0 must be an object"
            ):
                resolve_manual_batch(self.content_id, cwd=self.root)

    def test_valid_manual_resolution_still_discovers_relevant_manifest(self):
        self.write_valid_request()
        manifest = self.sourcing / "valid.json"
        manifest.write_text(
            json.dumps(
                {
                    "candidates": [
                        {"logical_id": "background-1"},
                        {"logical_id": "unrelated-background"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        with patch("engine.batch._run_git", side_effect=self.immutable_source):
            resolved = resolve_manual_batch(self.content_id, cwd=self.root)
        self.assertEqual(resolved.requests, (f"runtime/content/requests/{self.content_id}.json",))
        self.assertEqual(
            resolved.sourcing,
            ("runtime/content/background-sourcing/valid.json",),
        )


if __name__ == "__main__":
    unittest.main()
