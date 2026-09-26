import unittest

import analytics_remote


class AnalyticsRemoteScopeTests(unittest.TestCase):
    def test_planner_write_scope_is_exact(self):
        for path in (
            "content/analytics-summary.json",
            "content/planner-analytics.json",
            "content/analytics-index.json",
            "content/context.json",
        ):
            self.assertTrue(analytics_remote.allowed_path("planner", path), path)
        self.assertFalse(analytics_remote.allowed_path("planner", "content/analytics/snapshots/raw.json"))
        self.assertFalse(analytics_remote.allowed_path("planner", "PLANNING.md"))

    def test_warehouse_accepts_data_and_manifests_only(self):
        accepted = (
            "manifest/report-jobs.json",
            "manifest/collection-state.json",
            "manifest/schema-version.json",
            "raw/basic/channel_basic_a3/report.csv",
            "raw/basic/channel_basic_a3/report.metadata.json",
            "realtime/2026-09-26/analytics-20260926T120000Z.json",
            "realtime/legacy/analytics-20260901T000000Z.json",
            "retention/aaaaaaaaaaa/72h.json",
            "retention/aaaaaaaaaaa/7d.json",
        )
        for path in accepted:
            self.assertTrue(analytics_remote.allowed_path("warehouse", path), path)
        for path in (
            ".github/workflows/analytics.yml",
            "collector.py",
            "raw/basic/run.sh",
            "manifest/token.json",
            "README.md",
        ):
            self.assertFalse(analytics_remote.allowed_path("warehouse", path), path)


if __name__ == "__main__":
    unittest.main()
