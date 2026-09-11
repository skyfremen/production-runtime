import ast
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".github/workflows/run.yml"
CHECK = ROOT / ".github/workflows/check.yml"


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

    def test_check_workflow_has_no_environment_secrets(self):
        text = CHECK.read_text()
        self.assertNotIn("environment:", text)
        refs = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
        self.assertEqual(refs, {"GITHUB_TOKEN"})
        for forbidden in (
            "PRIVATE_STATE_TOKEN",
            "PRIVATE_STATE_REPOSITORY",
            "YOUTUBE_CLIENT_ID",
            "YOUTUBE_CLIENT_SECRET",
            "YOUTUBE_REFRESH_TOKEN",
            "PEXELS_API_KEY",
        ):
            self.assertNotIn(forbidden, text)

    def test_run_uses_generic_public_environment_aliases(self):
        text = RUN.read_text()
        for secret in (
            "YOUTUBE_CLIENT_ID",
            "YOUTUBE_CLIENT_SECRET",
            "YOUTUBE_REFRESH_TOKEN",
            "PEXELS_API_KEY",
        ):
            self.assertIn(f"secrets.{secret}", text)
        for alias in (
            "RUNTIME_AUTH_A",
            "RUNTIME_AUTH_B",
            "RUNTIME_AUTH_C",
            "RUNTIME_SOURCE_KEY",
        ):
            self.assertIn(f"{alias}:", text)
        for public_log_key in (
            "YOUTUBE_CLIENT_ID:",
            "YOUTUBE_CLIENT_SECRET:",
            "YOUTUBE_REFRESH_TOKEN:",
            "PEXELS_API_KEY:",
            "VIDEO_WIDTH:",
            "VIDEO_HEIGHT:",
            "VIDEO_FPS:",
            "STORY_TEST_MODE:",
            "SHORTS_CONCURRENCY:",
        ):
            self.assertNotIn(public_log_key, text)

    def test_workflow_display_labels_are_generic(self):
        visible = []
        for workflow in (RUN, CHECK):
            for line in workflow.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith("name:") or stripped.startswith("- name:") or "echo \"" in stripped:
                    visible.append(stripped.lower())
            self.assertNotIn("run-name:", workflow.read_text())
        rendered = "\n".join(visible)
        for forbidden in ("production", "private", "youtube", "wacky", "analytics", "recovery"):
            self.assertNotIn(forbidden, rendered)

    def test_runtime_does_not_target_public_contents(self):
        state = (ROOT / "runtime/transport.py").read_text()
        recovery = (ROOT / "runtime/output/state.py").read_text()
        self.assertIn('os.environ["PRIVATE_STATE_REPOSITORY"]', state)
        self.assertIn('os.environ["PRIVATE_STATE_REPOSITORY"]', recovery)
        self.assertNotIn('os.environ["GITHUB_REPOSITORY"]', recovery)

    def test_private_path_allowlist(self):
        import sys
        sys.path.insert(0, str(ROOT / "runtime"))
        from transport import TransportError, safe_private_path
        safe_private_path("youtube-shorts-bot/content/requests/opaque.json")
        safe_private_path("youtube-shorts-bot/media-library/backgrounds.json")
        for path in ("youtube-shorts-bot/analytics/latest.json", "README.md", "../secret"):
            with self.assertRaises(TransportError):
                safe_private_path(path)

    def test_source_sha_is_used_for_every_input_fetch(self):
        tree = ast.parse((ROOT / "runtime/transport.py").read_text())
        self.assertTrue(any(isinstance(node, ast.FunctionDef) and node.name == "bootstrap" for node in ast.walk(tree)))
        source = (ROOT / "runtime/transport.py").read_text()
        self.assertIn("store_file(state, path, source_sha)", source)
        self.assertIn('state.content(path, source_sha)', source)

    def test_public_paths_are_generic(self):
        paths = [
            path.relative_to(ROOT).as_posix().lower()
            for path in ROOT.rglob("*")
            if path.is_file()
            and ".git" not in path.relative_to(ROOT).parts
            and "__pycache__" not in path.relative_to(ROOT).parts
        ]
        visible = "\n".join(paths)
        for forbidden in (
            "/publishing/", "/rendering/", "/media/", "/planning/",
            "upload", "youtube", "short", "caption", "story",
        ):
            self.assertNotIn(forbidden, visible)

    def test_failure_codes_and_private_diagnostic_path_exist(self):
        workflow = RUN.read_text()
        transport = (ROOT / "runtime/transport.py").read_text()
        core = (ROOT / "runtime/core.py").read_text()
        self.assertIn("E_EXEC_001", workflow)
        self.assertIn("E_STATE_001", workflow)
        self.assertIn("DIAGNOSTIC_PREFIX", transport)
        self.assertIn("record_failure", core)


if __name__ == "__main__":
    unittest.main()
