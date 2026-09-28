import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import analytics


def video(age, views, engaged=50, shorts_views=0):
    return {
        "content_id": "wd-" + "a" * 24,
        "youtube_video_id": "aaaaaaaaaaa",
        "publish_at": "2026-09-25T08:00:00Z",
        "age_hours": age,
        "duration_seconds": 60,
        "creative": {
            "title": "A test story",
            "category": "workplace",
            "story_tone": "dramatic",
            "lead_gender": "female",
            "hook_type": "accusation",
            "trend_aware": False,
        },
        "metrics": {
            "views": views,
            "engaged_views": engaged,
            "average_view_duration": 40,
            "average_view_percentage": 66.7,
            "shorts_source_views": shorts_views,
            "shorts_source_engaged_views": min(engaged, shorts_views),
        },
    }


class CheckpointTests(unittest.TestCase):
    def test_performance_rows_adds_two_hour_checkpoint_and_actual_age(self):
        snapshots = [
            {"videos": [video(2.4, 120, shorts_views=100)]},
            {"videos": [video(6.2, 400, shorts_views=350)]},
            {"videos": [video(75, 900, shorts_views=800)]},
        ]

        row = analytics.performance_rows(snapshots)[0]

        self.assertEqual(row["views_2h"], 120)
        self.assertEqual(row["checkpoint_observations"]["2h"]["actual_age_hours"], 2.4)
        self.assertEqual(row["shorts_source_views"], 800)
        self.assertEqual(row["shorts_source_engaged_view_rate_percentage"], 6.25)


class OptionalAnalyticsTests(unittest.TestCase):
    def test_analytics_paginator_collects_two_pages_with_fixed_offsets(self):
        calls = []
        headers = [{"name": "country"}, {"name": "views"}]

        def report(params, token):
            calls.append((dict(params), token))
            if params["startIndex"] == 1:
                return {"kind": "analytics#resultTable", "columnHeaders": headers, "rows": [["US", i] for i in range(200)]}
            return {"columnHeaders": headers, "rows": [["SG", 201]]}

        result = analytics.analytics_report_all(
            {"ids": "channel==MINE", "maxResults": 7, "startIndex": 99},
            "token",
            report,
        )

        self.assertEqual([1, 201], [call[0]["startIndex"] for call in calls])
        self.assertEqual([200, 200], [call[0]["maxResults"] for call in calls])
        self.assertEqual("token", calls[1][1])
        self.assertEqual("analytics#resultTable", result["kind"])
        self.assertEqual(headers, result["columnHeaders"])
        self.assertEqual(201, len(result["rows"]))
        self.assertEqual(["SG", 201], result["rows"][-1])

    def test_analytics_paginator_probes_after_exact_full_page(self):
        starts = []

        def report(params, _token):
            starts.append(params["startIndex"])
            rows = [[index] for index in range(200)] if params["startIndex"] == 1 else []
            return {"columnHeaders": [{"name": "views"}], "rows": rows}

        result = analytics.analytics_report_all({}, "token", report)

        self.assertEqual([1, 201], starts)
        self.assertEqual(200, len(result["rows"]))

    def test_analytics_paginator_accepts_omitted_first_rows(self):
        result = analytics.analytics_report_all(
            {}, "token", lambda _params, _token: {"columnHeaders": [{"name": "views"}]}
        )
        self.assertEqual([], result["rows"])

    def test_analytics_paginator_rejects_header_drift(self):
        def report(params, _token):
            if params["startIndex"] == 1:
                return {"columnHeaders": [{"name": "views"}], "rows": [[index] for index in range(200)]}
            return {"columnHeaders": [{"name": "likes"}], "rows": [[1]]}

        with self.assertRaisesRegex(RuntimeError, "headers"):
            analytics.analytics_report_all({}, "token", report)

    def test_analytics_paginator_rejects_overfull_page(self):
        def report(_params, _token):
            return {"columnHeaders": [{"name": "views"}], "rows": [[index] for index in range(201)]}

        with self.assertRaisesRegex(RuntimeError, "page size"):
            analytics.analytics_report_all({}, "token", report)

    def test_analytics_paginator_raises_without_returning_partial_page(self):
        calls = []

        def report(params, _token):
            calls.append(params["startIndex"])
            if params["startIndex"] == 1:
                return {"columnHeaders": [{"name": "views"}], "rows": [[index] for index in range(200)]}
            raise RuntimeError("page two failed")

        with self.assertRaisesRegex(RuntimeError, "page two failed"):
            analytics.analytics_report_all({}, "token", report)
        self.assertEqual([1, 201], calls)

    def test_missing_data_api_counters_remain_null(self):
        original = analytics.youtube_data
        analytics.youtube_data = lambda *_args: {"items": [{"id": "aaaaaaaaaaa", "statistics": {}, "contentDetails": {}}]}
        try:
            result = analytics.collect_data_api([{"youtube_video_id": "aaaaaaaaaaa"}], "token")
        finally:
            analytics.youtube_data = original
        self.assertIsNone(result["aaaaaaaaaaa"]["views"])
        self.assertIsNone(result["aaaaaaaaaaa"]["likes"])
        self.assertIsNone(result["aaaaaaaaaaa"]["comments"])

    def test_core_analytics_rejection_falls_back_to_data_api_metrics(self):
        def rejected(_params, _token):
            raise RuntimeError("unsupported combination")

        original = analytics.analytics_report
        analytics.analytics_report = rejected
        try:
            rows, detailed, warnings = analytics.collect_analytics_api(
                [{"youtube_video_id": "aaaaaaaaaaa"}], "token",
                datetime(2026, 9, 26, tzinfo=timezone.utc),
            )
        finally:
            analytics.analytics_report = original

        self.assertEqual(rows, {})
        self.assertFalse(detailed)
        self.assertTrue(any("Data API metrics were still collected" in item for item in warnings))

    def test_optional_capability_failure_does_not_hide_supported_reports(self):
        calls = []

        def report_fn(params, _token):
            calls.append(params["dimensions"])
            if params["dimensions"].startswith("ageGroup"):
                raise RuntimeError("demographics unavailable")
            return {
                "columnHeaders": [{"name": "creatorContentType"}, {"name": "views"}],
                "rows": [["SHORTS", 100]],
            }

        reports, warnings = analytics.collect_optional_analytics_reports(
            "token", datetime(2026, 9, 26, tzinfo=timezone.utc), report_fn
        )

        self.assertIn("traffic_source", reports)
        self.assertIn("device_os", reports)
        self.assertNotIn("demographics", reports)
        self.assertTrue(any("demographics" in warning for warning in warnings))
        self.assertGreater(len(calls), 5)

    def test_per_video_shorts_source_is_named_accurately(self):
        def report_fn(_params, _token):
            return {
                "columnHeaders": [
                    {"name": "video"},
                    {"name": "insightTrafficSourceType"},
                    {"name": "views"},
                    {"name": "engagedViews"},
                ],
                "rows": [
                    ["aaaaaaaaaaa", "SHORTS", 80, 44],
                    ["aaaaaaaaaaa", "YT_SEARCH", 20, 10],
                ],
            }

        result, warnings = analytics.collect_per_video_traffic_sources(
            [{"youtube_video_id": "aaaaaaaaaaa"}], "token",
            datetime(2026, 9, 26, tzinfo=timezone.utc), report_fn,
        )

        self.assertEqual(warnings, [])
        self.assertEqual(result["aaaaaaaaaaa"]["shorts_source_views"], 80)
        self.assertEqual(result["aaaaaaaaaaa"]["shorts_source_engaged_view_rate_percentage"], 55.0)
        self.assertNotIn("shown_in_feed", json.dumps(result))

    def test_optional_reports_include_geography_and_device_page_two(self):
        def report_fn(params, _token):
            dimensions = params["dimensions"].split(",")
            metrics = params["metrics"].split(",")
            headers = [{"name": name} for name in dimensions + metrics]
            start = params.get("startIndex", 1)
            if params["dimensions"].startswith("country"):
                if start == 1:
                    return {"columnHeaders": headers, "rows": [["ZZ", "SHORTS", 0, 0, 0, 0, 0] for _ in range(200)]}
                return {"columnHeaders": headers, "rows": [["US", "SHORTS", 123, 80, 10, 5, 60]]}
            if params["dimensions"].startswith("deviceType"):
                if start == 1:
                    return {"columnHeaders": headers, "rows": [["UNKNOWN", "UNKNOWN", "SHORTS", 0, 0, 0, 0, 0] for _ in range(200)]}
                return {"columnHeaders": headers, "rows": [["MOBILE", "ANDROID", "SHORTS", 75, 50, 8, 6, 70]]}
            return {"columnHeaders": headers, "rows": []}

        reports, warnings = analytics.collect_optional_analytics_reports(
            "token", datetime(2026, 9, 26, tzinfo=timezone.utc), report_fn
        )

        self.assertEqual([], warnings)
        self.assertTrue(any(row["country"] == "US" for row in reports["geography"]["rows"]))
        self.assertTrue(any(row["deviceType"] == "MOBILE" for row in reports["device_os"]["rows"]))

    def test_optional_page_two_failure_discards_only_failed_dataset(self):
        def report_fn(params, _token):
            names = params["dimensions"].split(",") + params["metrics"].split(",")
            headers = [{"name": name} for name in names]
            start = params.get("startIndex", 1)
            if params["dimensions"].startswith("country"):
                if start > 1:
                    raise RuntimeError("second page unavailable")
                return {"columnHeaders": headers, "rows": [["ZZ", "SHORTS", 0, 0, 0, 0, 0] for _ in range(200)]}
            return {"columnHeaders": headers, "rows": []}

        reports, warnings = analytics.collect_optional_analytics_reports(
            "token", datetime(2026, 9, 26, tzinfo=timezone.utc), report_fn
        )

        self.assertNotIn("geography", reports)
        self.assertIn("device_os", reports)
        self.assertTrue(any("geography" in warning for warning in warnings))

    def test_per_video_shorts_source_includes_page_two(self):
        headers = [
            {"name": "video"},
            {"name": "insightTrafficSourceType"},
            {"name": "views"},
            {"name": "engagedViews"},
            {"name": "estimatedMinutesWatched"},
        ]

        def report_fn(params, _token):
            if params.get("startIndex", 1) == 1:
                return {"columnHeaders": headers, "rows": [["aaaaaaaaaaa", "YT_SEARCH", 1, 1, 1] for _ in range(200)]}
            return {"columnHeaders": headers, "rows": [["aaaaaaaaaaa", "SHORTS", 80, 44, 12]]}

        result, warnings = analytics.collect_per_video_traffic_sources(
            [{"youtube_video_id": "aaaaaaaaaaa"}],
            "token",
            datetime(2026, 9, 26, tzinfo=timezone.utc),
            report_fn,
        )

        self.assertEqual([], warnings)
        self.assertEqual(80, result["aaaaaaaaaaa"]["shorts_source_views"])


class DerivedArtifactTests(unittest.TestCase):
    def test_realtime_snapshot_preserves_reporting_and_retention_warnings(self):
        current = {
            "analytics_version": 3,
            "collected_at": "2026-09-26T12:00:00Z",
            "detailed_analytics_available": True,
            "analytics_reports": {},
            "warnings": ["snapshot warning"],
            "videos": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planner = root / "planner"
            warehouse = root / "warehouse"
            planner.mkdir()
            warehouse.mkdir()
            context = planner / "content" / "context.json"
            context.parent.mkdir()
            context.write_text("{}", encoding="utf-8")

            with patch.object(analytics, "access_token", return_value="token"), patch.object(
                analytics, "snapshot", return_value=current
            ), patch.object(
                analytics,
                "sync_reporting",
                return_value=SimpleNamespace(files=[], warnings=["reporting warning"]),
            ), patch.object(
                analytics,
                "collect_retention",
                return_value=SimpleNamespace(files=[], warnings=["retention warning"]),
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
                    datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
                )

            snapshot_path = next((warehouse / "realtime").glob("*/*.json"))
            stored = json.loads(snapshot_path.read_text(encoding="utf-8"))

        self.assertEqual(
            stored["warnings"],
            ["snapshot warning", "reporting warning", "retention warning"],
        )

    def test_index_selects_newest_snapshot_across_dated_and_legacy_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "realtime" / "legacy" / "analytics-20260925T120000Z.json"
            current = root / "realtime" / "2026-09-26" / "analytics-20260926T120000Z.json"
            legacy.parent.mkdir(parents=True)
            current.parent.mkdir(parents=True)
            legacy.write_text("{}", encoding="utf-8")
            current.write_text("{}", encoding="utf-8")

            index = analytics.build_analytics_index(
                root, {"generated_at": "2026-09-26T12:00:00Z"}, [],
            )

        self.assertEqual(
            index["latest_realtime_snapshot"],
            "realtime/2026-09-26/analytics-20260926T120000Z.json",
        )

    def test_index_advertises_targeted_datasets_stored_in_realtime_snapshot(self):
        snapshot = {
            "analytics_reports": {
                "traffic_source": {"rows": []},
                "playback_location": {"rows": []},
                "device_os": {"rows": []},
                "demographics": {"rows": []},
            },
            "videos": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "realtime" / "2026-09-26" / "analytics-20260926T120000Z.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(snapshot), encoding="utf-8")

            index = analytics.build_analytics_index(
                root, {"generated_at": "2026-09-26T12:00:00Z"}, [],
            )

        self.assertTrue(index["available_datasets"]["basic"])
        self.assertTrue(index["available_datasets"]["traffic_source"])
        self.assertTrue(index["available_datasets"]["playback_location"])
        self.assertTrue(index["available_datasets"]["device_os"])
        self.assertTrue(index["available_datasets"]["demographics"])
        self.assertFalse(index["available_datasets"]["reach"])

    def test_planner_projection_and_index_bound_warning_lists(self):
        warnings = [f"warning {i}" for i in range(20)]
        summary = {
            "generated_at": "2026-09-26T12:00:00Z",
            "learning": {"minimum_pattern_sample": 12, "stage": "established", "analytics_weight": "normal"},
            "warnings": warnings,
        }

        projection = analytics.planner_projection(summary)
        with tempfile.TemporaryDirectory() as tmp:
            index = analytics.build_analytics_index(Path(tmp), summary, warnings)

        self.assertEqual(projection["warnings"], warnings[:5])
        self.assertEqual(len(index["warnings"]), 9)
        self.assertEqual(index["warnings"][-1], "12 additional analytics warnings omitted")

    def test_supported_patterns_require_mature_checkpoint_sample_threshold(self):
        summary = {
            "generated_at": "2026-09-26T12:00:00Z",
            "learning": {"minimum_pattern_sample": 12, "stage": "established", "analytics_weight": "normal"},
            "category_performance": {
                "work": {"sample_size": 12, "sample_7d": 4, "median_views_7d": 900, "sample_72h": 12, "median_views_72h": 700},
            },
        }
        patterns = analytics.planner_projection(summary)["creative_signals"]["supported_patterns"]
        self.assertEqual(patterns[0]["evidence_median_views"], 700)

    def test_legacy_distribution_breakdowns_survive_v3_summary_backfill(self):
        current = {
            "distribution_breakdowns": {
                "geography": {"views_total": 10, "top": [{"country": "US", "views": 10}]},
                "traffic_sources": {"views_total": 10, "top": [{"source": "SHORTS", "views": 9}]},
            }
        }
        self.assertEqual(analytics.report_analysis(current, "geography", ("country",))["top"][0]["country"], "US")
        self.assertEqual(analytics.report_analysis(current, "traffic_source", ("insightTrafficSourceType",))["top"][0]["source"], "SHORTS")

    def test_detailed_summary_projection_and_index_have_separate_shapes(self):
        current = {
            "analytics_version": 3,
            "collected_at": "2026-09-26T12:00:00Z",
            "detailed_analytics_available": True,
            "analytics_reports": {
                "geography": {"rows": [{"country": "US", "creatorContentType": "SHORTS", "views": 100, "engagedViews": 60}]},
                "traffic_source": {"rows": [{"insightTrafficSourceType": "SHORTS", "creatorContentType": "SHORTS", "views": 90, "engagedViews": 55}]},
                "device_os": {"rows": [{"deviceType": "MOBILE", "operatingSystem": "ANDROID", "creatorContentType": "SHORTS", "views": 70, "engagedViews": 40}]},
            },
            "warnings": [],
            "videos": [video(75, 900, shorts_views=800)],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snap = root / "realtime" / "2026-09-26" / "analytics-20260926T120000Z.json"
            snap.parent.mkdir(parents=True)
            snap.write_text(json.dumps(current), encoding="utf-8")
            raw = root / "raw" / "traffic-source" / "channel_traffic_source_a3" / "r1.csv"
            raw.parent.mkdir(parents=True)
            raw.write_text("date,views\n2026-09-25,1\n", encoding="utf-8")

            summary = analytics.build_summary(root, current)
            projection = analytics.planner_projection(summary)
            index = analytics.build_analytics_index(root, summary, [])

        self.assertEqual(summary["analytics_version"], 3)
        self.assertIn("device_operating_system_analysis", summary)
        self.assertIn("shorts_feed_diagnostics", summary)
        self.assertIn("publish_hour_performance_sgt", summary)
        self.assertIn("publication_spacing_performance", summary)
        self.assertEqual(set(projection), {
            "analytics_version", "generated_at", "learning", "creative_signals",
            "distribution_signals", "audience_signals", "data_freshness", "warnings",
        })
        self.assertLess(len(json.dumps(projection).encode()), 16000)
        self.assertNotIn("videos", projection)
        self.assertEqual(index["analytics_repository"], "skyfremen/youtube-analytics-data")
        self.assertTrue(index["available_datasets"]["realtime"])
        self.assertTrue(index["available_datasets"]["traffic_source"])


if __name__ == "__main__":
    unittest.main()
