import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from engine.batch import BatchError, validate_explicit_batch
from engine.check import synthetic_request


def as_v5(payload):
    data = copy.deepcopy(payload)
    data["schema_version"] = 5
    data["visual"].update(
        {
            "background_primary_treatment": {
                "segment_start_seconds": 2.0,
                "segment_duration_seconds": 10.0,
                "playback_rate": 1.4,
            },
            "background_backup_treatment": {
                "segment_start_seconds": 0.0,
                "segment_duration_seconds": None,
                "playback_rate": 1.2,
            },
        }
    )
    return data


class V5BatchSourcingTests(unittest.TestCase):
    def fixture(self, root):
        request = as_v5(synthetic_request(0))
        request_path = root / f"{request['content_id']}.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        planning_path = root / "planning.json"
        planning_path.write_text(
            json.dumps(
                {
                    "plan_date": request["planning"]["plan_date"],
                    "final_selected": 1,
                    "content_ids": [request["content_id"]],
                }
            ),
            encoding="utf-8",
        )
        sourcing_path = root / "sourcing.json"
        sourcing_path.write_text(
            json.dumps(
                {
                    "plan_date": request["planning"]["plan_date"],
                    "candidates": [
                        {
                            "logical_id": request["visual"]["background_primary_id"],
                            "required_by_content_ids": [request["content_id"]],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return request, request_path, planning_path, sourcing_path

    def test_v5_treatment_objects_do_not_enter_background_id_set(self):
        with tempfile.TemporaryDirectory() as temporary:
            request, request_path, planning_path, sourcing_path = self.fixture(
                Path(temporary)
            )
            result = validate_explicit_batch(
                [request_path], [planning_path], [sourcing_path]
            )
        self.assertEqual(result.requests, (str(request_path),))
        self.assertEqual(request["schema_version"], 5)

    def test_malformed_sourcing_candidate_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            _request, request_path, planning_path, sourcing_path = self.fixture(
                Path(temporary)
            )
            sourcing_path.write_text(
                json.dumps(
                    {
                        "plan_date": "2099-09-10",
                        "candidates": [{"logical_id": {"unexpected": "object"}}],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(BatchError):
                validate_explicit_batch(
                    [request_path], [planning_path], [sourcing_path]
                )


if __name__ == "__main__":
    unittest.main()
