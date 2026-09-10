import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from production import pipeline as pipeline_module
from production.pipeline import (
    GenerationResult,
    PipelineError,
    PreparedRequest,
    ProductionPipeline,
    effective_cpu_count,
    parse_concurrency,
    read_requests,
    threads_per_worker,
)
from production.concurrency_benchmark import choose_winner, run_stage_a


class DummySampler:
    peak_cpu_percent = 0.0
    peak_load_1m = 0.0
    peak_rss_bytes = 0
    memory_total_bytes = 1

    def start(self):
        pass

    def stop(self):
        pass


class SimulatedPipeline(ProductionPipeline):
    def __init__(self, concurrency, root, *, fail_generation=None, fail_upload=None):
        super().__init__(
            concurrency,
            worker_root=root / "workers",
            base_env={"GITHUB_EVENT_NAME": "push", "GITHUB_SHA": "a" * 40},
            sampler=DummySampler(),
        )
        self.root = root
        self.fail_generation = fail_generation
        self.fail_upload = fail_upload
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        self.upload_order = []
        self.upload_threads = []
        self.output_dirs = []

    def prepare(self, index, request):
        content_id = Path(request).stem
        worker_dir = self.worker_root / f"{index:02d}-{content_id}"
        worker_dir.mkdir(parents=True)
        env = self.environment_for(request)
        self.output_dirs.append(env["STORY_OUTPUT_DIR"])
        return PreparedRequest(index, request, content_id, env, worker_dir, True)

    def generate(self, item):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        started = time.time()
        time.sleep(0.025)
        error = "simulated render failure" if item.content_id == self.fail_generation else None
        completed = time.time()
        with self.lock:
            self.active -= 1
        return GenerationResult(
            item,
            threading.current_thread().name,
            started,
            completed,
            {"media": 0.005, "render": 0.015, "verify": 0.005},
            error,
        )

    def upload(self, result):
        if result.error:
            raise PipelineError(result.error)
        if result.item.content_id == self.fail_upload:
            raise PipelineError("simulated upload failure")
        self.upload_order.append(result.item.request)
        self.upload_threads.append(threading.current_thread().name)
        self.defer_verification(result.item)


class ProductionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path_patches = []
        for name, relative in {
            "FAILURES_PATH": "failures.txt",
            "SKIPPED_PATH": "skipped.txt",
            "PENDING_PATH": "pending.txt",
            "PIPELINE_METRICS_PATH": "pipeline.tsv",
            "WORKER_METRICS_PATH": "workers.tsv",
            "RESOURCE_METRICS_PATH": "resources.tsv",
            "SUMMARY_PATH": "summary.json",
        }.items():
            current = patch.object(pipeline_module, name, self.root / relative)
            current.start()
            self.path_patches.append(current)
            self.addCleanup(current.stop)

    def requests(self, count):
        return [
            f"runtime/content/requests/wd-20990910T{index:02d}0000-test-a{index:05d}.json"
            for index in range(count)
        ]

    def test_only_local_generation_is_bounded_and_upload_is_serial(self):
        requests = self.requests(4)
        pipeline = SimulatedPipeline(2, self.root)
        summary = pipeline.run(requests)
        self.assertEqual(pipeline.max_active, 2)
        self.assertEqual(pipeline.upload_order, requests)
        self.assertEqual(pipeline.upload_threads, ["MainThread"] * 4)
        self.assertEqual(summary["pending_verification"], 4)
        self.assertEqual(summary["failed"], 0)

    def test_concurrency_one_preserves_sequential_order(self):
        requests = self.requests(3)
        pipeline = SimulatedPipeline(1, self.root)
        pipeline.run(requests)
        self.assertEqual(pipeline.max_active, 1)
        self.assertEqual(pipeline.upload_order, requests)

    def test_generation_failure_isolated_and_aggregation_is_deterministic(self):
        requests = self.requests(4)
        failed_id = Path(requests[1]).stem
        pipeline = SimulatedPipeline(2, self.root, fail_generation=failed_id)
        summary = pipeline.run(requests)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(
            pipeline.upload_order,
            [requests[0], requests[2], requests[3]],
        )
        failure = (self.root / "failures.txt").read_text(encoding="utf-8")
        self.assertIn(requests[1], failure)
        self.assertIn("generation failure", failure)

    def test_upload_failure_does_not_cancel_other_completed_renders(self):
        requests = self.requests(4)
        failed_id = Path(requests[2]).stem
        pipeline = SimulatedPipeline(2, self.root, fail_upload=failed_id)
        summary = pipeline.run(requests)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(
            pipeline.upload_order,
            [requests[0], requests[1], requests[3]],
        )
        self.assertEqual(summary["pending_verification"], 3)

    def test_each_worker_gets_an_isolated_environment_and_output_directory(self):
        pipeline = SimulatedPipeline(2, self.root)
        first, second = self.requests(2)
        env_a = pipeline.environment_for(first)
        env_b = pipeline.environment_for(second)
        self.assertIsNot(env_a, env_b)
        self.assertNotEqual(env_a["STORY_OUTPUT_DIR"], env_b["STORY_OUTPUT_DIR"])
        self.assertEqual(env_a["OMP_NUM_THREADS"], env_a["FFMPEG_THREADS"])
        self.assertEqual(env_a["TORCH_INTEROP_THREADS"], "1")

    def test_supported_concurrency_is_strictly_bounded(self):
        self.assertEqual([parse_concurrency(value) for value in ("1", "2", "3", "4")], [1, 2, 3, 4])
        for value in ("0", "5", "24", "bad"):
            with self.subTest(value=value), self.assertRaises(PipelineError):
                parse_concurrency(value)

    def test_default_thread_budget_prevents_worker_oversubscription(self):
        self.assertEqual(threads_per_worker(1, cpu_count=4), 4)
        self.assertEqual(threads_per_worker(2, cpu_count=4), 2)
        self.assertEqual(threads_per_worker(3, cpu_count=4), 1)
        self.assertEqual(threads_per_worker(4, cpu_count=4), 1)
        self.assertEqual(threads_per_worker(2, explicit="1", cpu_count=4), 1)

    def test_cpu_budget_uses_container_affinity(self):
        with patch("production.pipeline.os.sched_getaffinity", return_value={0, 1}):
            self.assertEqual(effective_cpu_count(), 2)

    def test_duplicate_content_id_is_rejected_before_any_worker_starts(self):
        path = self.root / "requests.txt"
        request = self.requests(1)[0]
        path.write_text(f"{request}\n{request}\n", encoding="utf-8")
        with self.assertRaisesRegex(PipelineError, "Duplicate content_id"):
            read_requests(path)

    def test_stage_a_stops_when_next_worker_adds_no_material_saving(self):
        measured = {
            1: {"concurrency": 1, "batch_seconds": 100.0, "failures": 0, "signatures": ["same"], "peak_memory_percent": 20.0},
            2: {"concurrency": 2, "batch_seconds": 61.0, "failures": 0, "signatures": ["same"], "peak_memory_percent": 30.0},
            3: {"concurrency": 3, "batch_seconds": 60.5, "failures": 0, "signatures": ["same"], "peak_memory_percent": 40.0},
            4: {"concurrency": 4, "batch_seconds": 58.0, "failures": 0, "signatures": ["same"], "peak_memory_percent": 50.0},
        }
        with patch(
            "production.concurrency_benchmark.run_candidate",
            side_effect=lambda concurrency, _requests, _root: dict(measured[concurrency]),
        ):
            results, winner = run_stage_a(["fixture"], self.root)
        self.assertEqual([item["concurrency"] for item in results], [1, 2, 3])
        self.assertEqual(winner, 2)

    def test_winner_is_lowest_level_within_three_percent_of_fastest(self):
        results = [
            {"concurrency": 1, "batch_seconds": 100.0, "failures": 0, "equivalent": True},
            {"concurrency": 2, "batch_seconds": 61.0, "failures": 0, "equivalent": True},
            {"concurrency": 3, "batch_seconds": 60.0, "failures": 0, "equivalent": True},
        ]
        self.assertEqual(choose_winner(results), 2)


if __name__ == "__main__":
    unittest.main()

