import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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


class DerivedArtifactTests(unittest.TestCase):
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
