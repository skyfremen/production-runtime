import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import analytics
from analytics_retention import collect_retention, due_retention_checkpoints


class RetentionTests(unittest.TestCase):
    def test_run_injects_paginator_and_preserves_more_than_200_curve_rows(self):
        headers = [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}]

        def api(params, _token):
            if params["startIndex"] == 1:
                return {"columnHeaders": headers, "rows": [[index / 200, 1.0] for index in range(200)]}
            return {"columnHeaders": headers, "rows": [[1.0, 0.2]]}

        paginator = analytics.analytics_report_all
        current = {
            "analytics_version": 3,
            "collected_at": "2026-09-26T00:00:00Z",
            "detailed_analytics_available": True,
            "analytics_reports": {},
            "warnings": [],
            "videos": [{
                "youtube_video_id": "aaaaaaaaaaa",
                "age_hours": 72,
                "publish_at": "2026-09-23T00:00:00Z",
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            planner = Path(tmp) / "planner"
            warehouse = Path(tmp) / "warehouse"
            (planner / "content").mkdir(parents=True)
            context = planner / "content/context.json"
            context.write_text("{}", encoding="utf-8")
            with patch.object(analytics, "access_token", return_value="token"), patch.object(
                analytics, "snapshot", return_value=current
            ), patch.object(
                analytics,
                "sync_reporting",
                return_value=SimpleNamespace(files=[], warnings=[]),
            ), patch.object(
                analytics,
                "analytics_report_all",
                side_effect=lambda params, token: paginator(params, token, api),
            ) as paginate, patch.object(
                analytics,
                "analytics_report",
                side_effect=lambda params, token: api(
                    {**params, "startIndex": 1, "maxResults": 200}, token
                ),
            ), patch.object(
                analytics, "build_summary", return_value={"generated_at": current["collected_at"], "warnings": []}
            ), patch.object(
                analytics, "planner_projection", return_value={}
            ), patch.object(
                analytics, "build_analytics_index", return_value={}
            ), patch.object(
                analytics, "rebuild_planner_context", return_value=context
            ):
                analytics.run(
                    planner,
                    warehouse,
                    datetime(2026, 9, 26, tzinfo=timezone.utc),
                )
            payload = json.loads((warehouse / "retention/aaaaaaaaaaa/72h.json").read_text())

        self.assertEqual(201, len(payload["rows"]))
        self.assertTrue(paginate.called)

    def test_core_metric_fallback_is_paginated(self):
        headers = [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}]

        def api(params, _token):
            if "startedWatching" in params["metrics"]:
                return {"columnHeaders": headers, "rows": []}
            if params["startIndex"] == 1:
                return {"columnHeaders": headers, "rows": [[index / 200, 1.0] for index in range(200)]}
            return {"columnHeaders": headers, "rows": [[1.0, 0.2]]}

        with tempfile.TemporaryDirectory() as tmp:
            state = {"retention_checkpoints": {}}
            result = collect_retention(
                [{"youtube_video_id": "aaaaaaaaaaa", "age_hours": 72}],
                state,
                Path(tmp),
                lambda params: analytics.analytics_report_all(params, "token", api),
                datetime(2026, 9, 26, tzinfo=timezone.utc),
            )
            payload = json.loads(result.files[0].read_text())

        self.assertEqual(201, len(payload["rows"]))

    def test_page_two_failure_writes_no_curve_or_completed_checkpoint(self):
        headers = [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}]

        def api(params, _token):
            if params["startIndex"] == 1:
                return {"columnHeaders": headers, "rows": [[index / 200, 1.0] for index in range(200)]}
            raise RuntimeError("page two failed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {"retention_checkpoints": {}}
            result = collect_retention(
                [{"youtube_video_id": "aaaaaaaaaaa", "age_hours": 72}],
                state,
                root,
                lambda params: analytics.analytics_report_all(params, "token", api),
                datetime(2026, 9, 26, tzinfo=timezone.utc),
            )

            self.assertEqual([], result.files)
            self.assertFalse((root / "retention/aaaaaaaaaaa/72h.json").exists())
        self.assertEqual({}, state["retention_checkpoints"])
        self.assertTrue(any("RuntimeError" in warning for warning in result.warnings))

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
            videos = [{
                "youtube_video_id": "aaaaaaaaaaa",
                "age_hours": 76.25,
                "publish_at": "2026-09-22T08:00:00Z",
            }]
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
            self.assertEqual(calls[0]["startDate"], "2026-09-22")

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

    def test_empty_curve_warns_and_remains_due_for_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = {"retention_checkpoints": {}}
            result = collect_retention(
                [{"youtube_video_id": "aaaaaaaaaaa", "age_hours": 72}],
                state, Path(tmp), lambda _params: {"columnHeaders": [], "rows": []},
                datetime(2026, 9, 26, tzinfo=timezone.utc),
            )

            self.assertEqual(result.files, [])
            self.assertTrue(any("empty" in warning for warning in result.warnings))
            self.assertEqual(state["retention_checkpoints"], {})

    def test_empty_granular_query_retries_with_core_retention_metrics(self):
        calls = []

        def query(params):
            calls.append(params["metrics"])
            if "startedWatching" in params["metrics"]:
                return {"columnHeaders": [], "rows": []}
            return {
                "columnHeaders": [
                    {"name": "elapsedVideoTimeRatio"},
                    {"name": "audienceWatchRatio"},
                    {"name": "relativeRetentionPerformance"},
                ],
                "rows": [[0.01, 1.0, 0.5], [1.0, 0.2, 0.4]],
            }

        with tempfile.TemporaryDirectory() as tmp:
            state = {"retention_checkpoints": {}}
            result = collect_retention(
                [{
                    "youtube_video_id": "aaaaaaaaaaa",
                    "age_hours": 72,
                    "publish_at": "2026-09-23T00:00:00Z",
                }],
                state, Path(tmp), query,
                datetime(2026, 9, 26, tzinfo=timezone.utc),
            )

            self.assertEqual(len(calls), 2)
            self.assertEqual(
                calls[1],
                "audienceWatchRatio,relativeRetentionPerformance",
            )
            self.assertEqual(len(result.files), 1)
            self.assertEqual(result.warnings, [])

    def test_query_budget_prioritizes_untried_checkpoints_and_aggregates_empty_warnings(self):
        calls = []

        def empty_query(params):
            calls.append(params["filters"].removeprefix("video=="))
            return {"columnHeaders": [], "rows": []}

        videos = [
            {"youtube_video_id": f"v{i:010d}", "age_hours": 72 + i}
            for i in range(4)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            state = {"retention_checkpoints": {}}
            first = collect_retention(
                videos, state, Path(tmp), empty_query,
                datetime(2026, 9, 26, tzinfo=timezone.utc), max_queries=2,
            )
            first_calls = set(calls)
            calls.clear()
            second = collect_retention(
                videos, state, Path(tmp), empty_query,
                datetime(2026, 9, 26, 6, tzinfo=timezone.utc), max_queries=2,
            )

            self.assertEqual(len(first_calls), 2)
            self.assertEqual(len(set(calls)), 2)
            self.assertTrue(first_calls.isdisjoint(calls))
            self.assertEqual(first.warnings, [
                "Optional retention returned empty curves for 2 due checkpoints; will retry",
            ])
            self.assertEqual(second.warnings, [
                "Optional retention returned empty curves for 2 due checkpoints; will retry",
            ])
            self.assertEqual(len(state["retention_attempts"]), 4)


if __name__ == "__main__":
    unittest.main()
