import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".github" / "workflows" / "run.yml"


class WorkflowRecoveryEdgeTests(unittest.TestCase):
    def test_daily_readiness_runs_before_shared_preparation_and_fanout(self):
        text = RUN.read_text(encoding="utf-8")
        readiness = text.index("- name: Readiness")
        shared = text.index("- name: Shared preparation")
        plan = text.index("- name: Plan")
        self.assertLess(readiness, shared)
        self.assertLess(shared, plan)
        self.assertIn("- name: Record preparation failure", text)

    def test_missing_unit_artifacts_do_not_bypass_fail_closed_aggregation(self):
        text = RUN.read_text(encoding="utf-8")
        aggregate = text.split("\n  aggregate:\n", 1)[1]
        import_block = aggregate.split("- name: Import", 1)[1].split("- name: Aggregate", 1)[0]
        self.assertIn("continue-on-error: true", import_block)
        self.assertIn("actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093", import_block)
        self.assertIn("python runtime/engine/aggregate.py", aggregate)


if __name__ == "__main__":
    unittest.main()
