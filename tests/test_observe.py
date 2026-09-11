import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import ANY, patch

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

import observe
import state_sink


class ObservationTests(unittest.TestCase):
    def test_collect_uses_singapore_date_window_and_raw_sources(self):
        now = datetime(2026, 9, 11, 6, 30, tzinfo=timezone.utc)
        with patch("observe._credentials", return_value=object()), \
             patch("observe.collect_aggregate", return_value=[{"video": "a"}]) as aggregate, \
             patch("observe.collect_recent", return_value=[{"video": "b"}]) as recent:
            payload = observe.collect(now)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["captured_at"], "2026-09-11T06:30:00Z")
        self.assertEqual(payload["window"], {
            "start_date": "2026-06-13",
            "end_date": "2026-09-11",
        })
        aggregate.assert_called_once_with(ANY, "2026-06-13", "2026-09-11")
        recent.assert_called_once_with(ANY, now)

    def test_sink_rejects_rows_without_video_identity(self):
        payload = {
            "schema_version": 1,
            "captured_at": "2026-09-11T06:30:00Z",
            "window": {"start_date": "2026-06-13", "end_date": "2026-09-11"},
            "aggregate": [{"views": 1}],
            "recent": [],
        }
        with self.assertRaises(state_sink.SinkError):
            state_sink._validate(payload)

    def test_sink_accepts_minimal_valid_snapshot(self):
        payload = {
            "schema_version": 1,
            "captured_at": "2026-09-11T06:30:00Z",
            "window": {"start_date": "2026-06-13", "end_date": "2026-09-11"},
            "aggregate": [{"video": "a"}],
            "recent": [{"video": "b"}],
        }
        self.assertIs(state_sink._validate(payload), payload)


if __name__ == "__main__":
    unittest.main()
