import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from engine.shard import ShardError, partition_requests, select_shard, shard_plan


class ShardTests(unittest.TestCase):
    def manifest(self, count, root):
        requests = []
        for index in range(count):
            path = root / f"item-{index:02d}.json"
            path.write_text(json.dumps({
                "publication": {"publish_at": f"2026-01-01T{index:02d}:00:00Z"}
            }))
            requests.append(str(path))
        return {"requests": requests, "planning": [], "sourcing": []}

    def test_twenty_four_items_become_twelve_stable_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(24, Path(directory))
            first = shard_plan(manifest, "paired")
            second = shard_plan(manifest, "paired")
            self.assertEqual(first, second)
            self.assertEqual(first["shard_count"], 12)
            ordered = tuple(manifest["requests"])
            shards = partition_requests(ordered)
            self.assertEqual([len(shard) for shard in shards], [2] * 12)
            self.assertEqual(tuple(item for shard in shards for item in shard), ordered)

    def test_smaller_batches_are_bounded_and_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for count, expected_sizes in ((1, [1]), (2, [2]), (3, [2, 1]), (7, [2, 2, 2, 1])):
                manifest = self.manifest(count, root)
                shards = partition_requests(tuple(manifest["requests"]))
                self.assertEqual([len(shard) for shard in shards], expected_sizes)
                self.assertEqual(shard_plan(manifest, "paired")["shard_count"], len(expected_sizes))

    def test_single_profile_is_exactly_one_item_and_concurrency_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.manifest(1, root)
            plan = shard_plan(manifest, "single")
            self.assertEqual(plan["matrix"], {"include": [{"index": 0}]})
            self.assertEqual(plan["concurrency"], 1)
            self.assertEqual(select_shard(tuple(manifest["requests"]), 0, "single"), tuple(manifest["requests"]))
            with self.assertRaises(ShardError):
                shard_plan(self.manifest(2, root), "single")


if __name__ == "__main__":
    unittest.main()
