import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

import core


class FakePipeline:
    seen = None

    def __init__(self, concurrency, base_env=None):
        self.concurrency = concurrency

    def run(self, requests):
        FakePipeline.seen = tuple(requests)
        return {
            "failed": 0,
            "skipped": len(requests),
            "concurrency": self.concurrency,
            "production_phase_seconds": 1.0,
            "peak_cpu_percent": 2.0,
            "peak_memory_percent": 3.0,
        }


class CoreShardTests(unittest.TestCase):
    def test_pipeline_consumes_only_authoritative_selected_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ordered = tuple(str(root / f"item-{index}.json") for index in range(4))
            manifest = {"request_sources": {path: {} for path in ordered}}
            summary = root / "summary.json"
            pending = root / "pending"
            internal = root / "internal.log"
            with (
                patch("core.prepare_manifest", return_value=(manifest, ordered)),
                patch("core.ProductionPipeline", FakePipeline),
                patch("core.silent_call"),
                patch.object(core, "PUBLIC_SUMMARY", summary),
                patch.object(core, "PENDING", pending),
                patch.object(core, "INTERNAL_LOG", internal),
            ):
                core.run(
                    root / "manifest.json", 2, profile="paired", shard_index=1,
                    persist_registry=False,
                )
            self.assertEqual(FakePipeline.seen, ordered[2:4])
            payload = json.loads(summary.read_text())
            self.assertEqual(payload["shard_index"], 1)
            self.assertEqual(payload["request_count"], 2)


if __name__ == "__main__":
    unittest.main()
