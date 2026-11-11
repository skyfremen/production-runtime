import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import analytics_remote


class AnalyticsRemoteScopeTests(unittest.TestCase):
    def test_ref_update_422_is_retryable_concurrency_conflict(self):
        repo = analytics_remote.GitHubRepository("owner/repo", "token", "planner")
        with patch("analytics_remote.urlopen", side_effect=HTTPError("url", 422, "race", {}, None)):
            with self.assertRaises(analytics_remote.RefAdvanced):
                repo.request("git/refs/heads/main", "PATCH", {"sha": "a" * 40, "force": False})

    def test_concurrent_planner_updates_recover_after_three_conflicts(self):
        planner = Mock()
        warehouse = Mock()
        planner.head_sha.return_value = "a" * 40
        warehouse.head_sha.return_value = "b" * 40
        planner.download_tree.side_effect = lambda sha, target: target
        warehouse.download_tree.side_effect = lambda sha, target: target
        warehouse.commit_files.return_value = "c" * 40
        planner.commit_files.side_effect = [
            analytics_remote.RefAdvanced("planner main advanced"),
            analytics_remote.RefAdvanced("planner main advanced"),
            analytics_remote.RefAdvanced("planner main advanced"),
            "d" * 40,
        ]
        runner = Mock(return_value=SimpleNamespace(planner_files=[], warehouse_files=[]))
        with patch("analytics_remote.time.sleep") as sleep:
            analytics_remote.run_remote(planner, warehouse, runner=runner)
        self.assertEqual(runner.call_count, 4)
        self.assertEqual(planner.commit_files.call_count, 4)
        self.assertEqual(sleep.call_args_list[0].args, (5,))
        self.assertEqual(sleep.call_args_list[1].args, (10,))
        self.assertEqual(sleep.call_args_list[2].args, (15,))

    def test_retry_limit_terminates_after_eight_conflicts(self):
        planner = Mock()
        warehouse = Mock()
        planner.head_sha.return_value = "a" * 40
        warehouse.head_sha.return_value = "b" * 40
        planner.download_tree.side_effect = lambda sha, target: target
        warehouse.download_tree.side_effect = lambda sha, target: target
        warehouse.commit_files.return_value = "c" * 40
        planner.commit_files.side_effect = analytics_remote.RefAdvanced("planner main advanced")
        runner = Mock(return_value=SimpleNamespace(planner_files=[], warehouse_files=[]))
        with patch("analytics_remote.time.sleep") as sleep:
            with self.assertRaisesRegex(SystemExit, "after 8 attempts"):
                analytics_remote.run_remote(planner, warehouse, runner=runner)
        self.assertEqual(planner.commit_files.call_count, 8)
        self.assertEqual(sleep.call_count, 7)

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
