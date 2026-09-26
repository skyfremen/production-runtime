import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from analytics_retention import collect_retention, due_retention_checkpoints


class RetentionTests(unittest.TestCase):
    def test_only_due_checkpoint_is_selected_and_completed_is_skipped(self):
        videos = [
            {"youtube_video_id": "aaaaaaaaaaa", "age_hours": 80},
            {"youtube_video_id": "bbbbbbbbbbb", "age_hours": 170},
            {"youtube_video_id": "ccccccccccc", "age_hours": 20},
            {"youtube_video_id": "ddddddddddd", "age_hours": 140},
        ]
        completed = {"aaaaaaaaaaa": {"72h": {"path": "already"}}}

        due = due_retention_checkpoints(videos, completed)

        self.assertEqual(due, [("bbbbbbbbbbb", "7d", 170.0)])

    def test_collection_persists_actual_age_and_does_not_duplicate_checkpoint(self):
        calls = []

        def query(params):
            calls.append(params)
            return {
                "columnHeaders": [
                    {"name": "elapsedVideoTimeRatio"},
                    {"name": "audienceWatchRatio"},
                ],
                "rows": [[0.0, 1.0], [0.5, 0.61], [1.0, 0.22]],
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {"retention_checkpoints": {}}
            videos = [{"youtube_video_id": "aaaaaaaaaaa", "age_hours": 76.25}]
            first = collect_retention(
                videos, state, root, query,
                datetime(2026, 9, 26, tzinfo=timezone.utc),
            )
            second = collect_retention(
                videos, state, root, query,
                datetime(2026, 9, 26, tzinfo=timezone.utc),
            )

            self.assertEqual(len(calls), 1)
            self.assertEqual(len(first.files), 1)
            self.assertEqual(second.files, [])
            payload = json.loads(first.files[0].read_text())
            self.assertEqual(payload["checkpoint"], "72h")
            self.assertEqual(payload["observed_age_hours"], 76.25)
            self.assertIn("startedWatching", calls[0]["metrics"])

    def test_optional_query_failure_warns_and_does_not_mark_complete(self):
        def query(_params):
            raise RuntimeError("not enough retention data")

        with tempfile.TemporaryDirectory() as tmp:
            state = {"retention_checkpoints": {}}
            result = collect_retention(
                [{"youtube_video_id": "aaaaaaaaaaa", "age_hours": 72}],
                state, Path(tmp), query,
                datetime(2026, 9, 26, tzinfo=timezone.utc),
            )

            self.assertEqual(result.files, [])
            self.assertTrue(result.warnings)
            self.assertEqual(state["retention_checkpoints"], {})


if __name__ == "__main__":
    unittest.main()
