import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from engine.batch import BatchError, order_requests, validate_explicit_batch
from engine.check import FIXTURE_DATE, build_synthetic_batch


class ProductionBatchTests(unittest.TestCase):
    def test_duplicate_content_id_is_rejected_before_processing(self):
        _root, requests, _planning = self.fixture()
        request = requests[0]
        with self.assertRaisesRegex(BatchError, "Duplicate content_id"):
            order_requests([request, request])

    def fixture(self):
        holder = tempfile.TemporaryDirectory()
        root = Path(holder.name)
        requests, planning, _payloads = build_synthetic_batch(root)
        self.addCleanup(holder.cleanup)
        return root, requests, planning

    def test_full_24_item_batch_uses_shared_production_validation(self):
        _root, requests, planning = self.fixture()
        batch = validate_explicit_batch(requests, [planning])
        ordered = order_requests(batch.requests)
        self.assertEqual(len(ordered), 24)
        self.assertEqual(len(set(ordered)), 24)

    def test_duplicate_publication_slot_is_batch_fatal(self):
        _root, requests, planning = self.fixture()
        second = Path(requests[1])
        payload = json.loads(second.read_text(encoding="utf-8"))
        first_payload = json.loads(Path(requests[0]).read_text(encoding="utf-8"))
        payload["publication"]["publish_at"] = first_payload["publication"]["publish_at"]
        second.write_text(json.dumps(payload), encoding="utf-8")
        batch = validate_explicit_batch(requests, [planning])
        with self.assertRaisesRegex(BatchError, "Duplicate publication slot"):
            order_requests(batch.requests)

    def test_planning_content_ids_must_match_exactly(self):
        _root, requests, planning = self.fixture()
        plan_path = Path(planning)
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        payload["content_ids"] = payload["content_ids"][:-1]
        plan_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(BatchError, "content_ids do not exactly match"):
            validate_explicit_batch(requests, [planning])

    def test_malformed_request_is_deferred_to_per_video_validation(self):
        _root, requests, planning = self.fixture()
        malformed = Path(requests[12])
        malformed.write_text("{ definitely not json", encoding="utf-8")
        batch = validate_explicit_batch(requests, [planning])
        ordered = order_requests(batch.requests)
        self.assertEqual(len(ordered), 24)
        self.assertEqual(ordered[-1], str(malformed))

    def test_sourcing_manifest_cannot_claim_unreferenced_background(self):
        root, requests, planning = self.fixture()
        sourcing = root / "content" / "background-sourcing" / f"{FIXTURE_DATE}.json"
        sourcing.parent.mkdir(parents=True)
        sourcing.write_text(
            json.dumps(
                {
                    "plan_date": FIXTURE_DATE,
                    "candidates": [
                        {
                            "logical_id": "satisfying-999",
                            "required_by_content_ids": [Path(requests[0]).stem],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(BatchError, "Unreferenced sourced background"):
            validate_explicit_batch(requests, [planning], [str(sourcing)])

    def test_batch_size_above_24_is_rejected(self):
        _root, requests, planning = self.fixture()
        with self.assertRaisesRegex(BatchError, "1-24 requests"):
            validate_explicit_batch(requests + [requests[0]], [planning])


if __name__ == "__main__":
    unittest.main()

