import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".github/workflows/run.yml"


class BoundaryTests(unittest.TestCase):
    def test_normal_dispatch_has_only_opaque_contract(self):
        text = RUN.read_text()
        block = text.split("inputs:", 1)[1].split("concurrency:", 1)[0]
        self.assertIn("batch_id:", block)
        self.assertIn("source_sha:", block)
        for forbidden in ("content_ids:", "request:", "story:", "title:", "schedule:"):
            self.assertNotIn(forbidden, block)

    def test_trigger_and_permissions_are_read_only(self):
        text = RUN.read_text()
        trigger = text.split("on:", 1)[1].split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        for forbidden in ("\n  push:", "pull_request:", "pull_request_target:", "\n  schedule:", "repository_dispatch:"):
            self.assertNotIn(forbidden, trigger)
        permissions = text.split("permissions:", 1)[1].split("jobs:", 1)[0]
        self.assertIn("contents: read", permissions)
        self.assertIn("packages: read", permissions)
        self.assertNotIn("write", permissions)

    def test_no_artifact_or_unsafe_shell_logging(self):
        workflows = "\n".join(path.read_text() for path in (ROOT / ".github/workflows").glob("*.yml"))
        self.assertNotIn("upload-artifact", workflows)
        for unsafe in ("set -x", "printenv", "curl -v", "cat request", "Authorization:"):
            self.assertNotIn(unsafe, workflows)

    def test_all_actions_are_full_sha_pinned(self):
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            for line in workflow.read_text().splitlines():
                if "uses:" in line:
                    ref = line.split("@", 1)[-1].split()[0]
                    self.assertRegex(ref, r"^[0-9a-f]{40}$")

    def test_runtime_does_not_target_public_contents(self):
        state = (ROOT / "runtime/state_transport.py").read_text()
        recovery = (ROOT / "runtime/publishing/recovery_state.py").read_text()
        self.assertIn('os.environ["PRIVATE_STATE_REPOSITORY"]', state)
        self.assertIn('os.environ["PRIVATE_STATE_REPOSITORY"]', recovery)
        self.assertNotIn('os.environ["GITHUB_REPOSITORY"]', recovery)

    def test_private_path_allowlist(self):
        import sys
        sys.path.insert(0, str(ROOT / "runtime"))
        from state_transport import TransportError, safe_private_path
        safe_private_path("youtube-shorts-bot/content/requests/opaque.json")
        safe_private_path("youtube-shorts-bot/media-library/backgrounds.json")
        for path in ("youtube-shorts-bot/analytics/latest.json", "README.md", "../secret"):
            with self.assertRaises(TransportError):
                safe_private_path(path)

    def test_source_sha_is_used_for_every_input_fetch(self):
        tree = ast.parse((ROOT / "runtime/state_transport.py").read_text())
        self.assertTrue(any(isinstance(node, ast.FunctionDef) and node.name == "bootstrap" for node in ast.walk(tree)))
        source = (ROOT / "runtime/state_transport.py").read_text()
        self.assertIn("store_file(state, path, source_sha)", source)
        self.assertIn('state.content(path, source_sha)', source)


if __name__ == "__main__":
    unittest.main()

