import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".github/workflows/run.yml"
SINGLE = ROOT / ".github/workflows/single.yml"


def start_block(text):
    marker = "name: Start"
    if marker not in text:
        raise AssertionError("START step is missing")
    tail = text.split(marker, 1)[1]
    return tail.split("name: Source", 1)[0]


def embedded_python(text):
    block = start_block(text)
    script = block.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    return textwrap.dedent(script)


class StartHeartbeatWorkflowTests(unittest.TestCase):
    def test_dispatch_contract_includes_exact_correlation_id(self):
        for workflow in (RUN, SINGLE):
            text = workflow.read_text(encoding="utf-8")
            inputs = text.split("inputs:", 1)[1].split("concurrency:", 1)[0]
            self.assertIn("batch_id:", inputs)
            self.assertIn("source_sha:", inputs)
            self.assertIn("contract_hash:", inputs)
            self.assertIn("dispatch_id:", inputs)
            self.assertIn("dispatch_id: {description: Correlation identifier, required: true", inputs)

    def test_start_is_first_executable_step(self):
        for workflow in (RUN, SINGLE):
            text = workflow.read_text(encoding="utf-8")
            jobs = text.split("jobs:\n", 1)[1]
            start = jobs.index("name: Start")
            source = jobs.index("name: Source")
            compatibility = jobs.index("name: Compatibility")
            load = jobs.index("name: Load")
            execute = jobs.index("name: Execute")
            self.assertLess(start, source)
            self.assertLess(start, compatibility)
            self.assertLess(start, load)
            self.assertLess(start, execute)

    def test_start_script_compiles_and_uses_append_only_private_evidence(self):
        for workflow in (RUN, SINGLE):
            text = workflow.read_text(encoding="utf-8")
            script = embedded_python(text)
            compile(script, str(workflow), "exec")
            for required in (
                "dispatch-intents",
                "content/recovery/starts",
                "schema_version",
                "state': 'prepared",
                "state': 'started",
                "START_BATCH_ID",
                "START_SOURCE_SHA",
                "START_CONTRACT_HASH",
                "START_DISPATCH_ID",
                "GITHUB_RUN_ID",
                "GITHUB_RUN_ATTEMPT",
                "E_START_001",
                "b'\\x00'",
            ):
                self.assertIn(required, script)
            self.assertNotIn("print(intent", script)
            self.assertNotIn("print(payload", script)
            self.assertNotIn("print(token", script)

    def test_start_failure_cannot_fall_through_into_side_effect_work(self):
        run = RUN.read_text(encoding="utf-8")
        single = SINGLE.read_text(encoding="utf-8")
        self.assertNotIn("continue-on-error: true", start_block(run))
        self.assertNotIn("continue-on-error: true", start_block(single))
        aggregate = run.split("  aggregate:\n", 1)[1]
        self.assertIn("needs.prepare.result == 'success'", aggregate.split("runs-on:", 1)[0])

    def test_same_batch_concurrency_is_shared_by_both_paths(self):
        for workflow in (RUN, SINGLE):
            text = workflow.read_text(encoding="utf-8")
            self.assertIn("group: run-${{ inputs.batch_id }}", text)
            self.assertIn("cancel-in-progress: false", text)


if __name__ == "__main__":
    unittest.main()
