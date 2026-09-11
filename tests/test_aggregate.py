import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from engine.aggregate import aggregate_summaries


def unit_summary(index, count, *, success=None, failed=0, skipped=0, concurrency=2):
    return {
        "request_count": count,
        "success": count - failed - skipped if success is None else success,
        "failed": failed,
        "skipped": skipped,
        "concurrency": concurrency,
        "production_phase_seconds": 10.0 + index,
        "peak_cpu_percent": 30.0 + index,
        "peak_memory_percent": 20.0 + index,
        "shard_index": index,
    }


class AggregateTests(unittest.TestCase):
    def fixture(self, count, root):
        requests = []
        for index in range(count):
            path = root / f"item-{index:02d}.json"
            path.write_text(json.dumps({
                "publication": {"publish_at": f"2026-01-01T{index:02d}:00:00Z"}
            }))
            requests.append(str(path))
        return {"requests": requests, "planning": [], "sourcing": []}

    def write_unit(self, root, index, summary):
        target = root / f"unit-{index}"
        target.mkdir()
        (target / "runtime-public-summary.json").write_text(json.dumps(summary))

    def test_combines_success_skip_and_failure_without_losing_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(4, root)
            self.write_unit(root, 0, unit_summary(0, 2, skipped=1))
            self.write_unit(root, 1, unit_summary(1, 2, failed=1))
            result = aggregate_summaries(manifest, root, "paired")
            self.assertEqual((result["success"], result["failed"], result["skipped"]), (2, 1, 1))
            self.assertEqual(result["failed_shards"], 1)

    def test_missing_or_invalid_unit_fails_only_its_assigned_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(3, root)
            self.write_unit(root, 0, unit_summary(0, 2))
            result = aggregate_summaries(manifest, root, "paired")
            self.assertEqual((result["success"], result["failed"]), (2, 1))
            self.assertEqual(result["completed_shards"], 1)

    def test_single_profile_aggregates_one_concurrency_one_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(1, root)
            self.write_unit(root, 0, unit_summary(0, 1, concurrency=1))
            result = aggregate_summaries(manifest, root, "single")
            self.assertEqual((result["success"], result["failed"], result["shard_count"]), (1, 0, 1))


if __name__ == "__main__":
    unittest.main()
