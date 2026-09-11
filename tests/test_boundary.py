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

    def test_only_sanitized_result_artifacts_and_no_unsafe_shell_logging(self):
        workflows = "\n".join(path.read_text() for path in (ROOT / ".github/workflows").glob("*.yml"))
        self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", workflows)
        self.assertIn("actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093", workflows)
        self.assertIn("path: /tmp/runtime-public-summary.json", workflows)
        for forbidden_path in ("runtime/content", "runtime/output", "/tmp/runtime-batch.json\n          retention"):
            self.assertNotIn(forbidden_path, workflows)
        for unsafe in ("set -x", "printenv", "curl -v", "cat request", "Authorization:"):
            self.assertNotIn(unsafe, workflows)

    def test_all_actions_are_full_sha_pinned(self):
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            for line in workflow.read_text().splitlines():
                if "uses:" in line:
                    ref = line.split("@", 1)[-1].split()[0]
                    self.assertRegex(ref, r"^[0-9a-f]{40}$")

    def test_check_mode_remains_secret_free_and_observe_is_minimal(self):
        text = CHECK.read_text()
        check_block = text.split("  check:\n", 1)[1].split("\n  observe:\n", 1)[0]
        observe_block = text.split("\n  observe:\n", 1)[1]

        self.assertNotIn("environment:", check_block)
        check_refs = set(re.findall(r"secrets\.([A-Z0-9_]+)", check_block))
        self.assertEqual(check_refs, {"GITHUB_TOKEN"})
        for forbidden in (
            "PRIVATE_STATE_TOKEN",
            "PRIVATE_STATE_REPOSITORY",
            "YOUTUBE_CLIENT_ID",
            "YOUTUBE_CLIENT_SECRET",
            "YOUTUBE_REFRESH_TOKEN",
            "PEXELS_API_KEY",
        ):
            self.assertNotIn(forbidden, check_block)

        self.assertIn("environment: exec", observe_block)
        observe_refs = set(re.findall(r"secrets\.([A-Z0-9_]+)", observe_block))
        self.assertEqual(
            observe_refs,
            {
                "GITHUB_TOKEN",
                "PRIVATE_STATE_TOKEN",
                "PRIVATE_STATE_REPOSITORY",
                "YOUTUBE_CLIENT_ID",
                "YOUTUBE_CLIENT_SECRET",
                "YOUTUBE_REFRESH_TOKEN",
            },
        )
        self.assertNotIn("PEXELS_API_KEY", observe_block)

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

    def test_bounded_matrix_and_single_finalizer(self):
        workflow = RUN.read_text()
        self.assertIn("fail-fast: false", workflow)
        self.assertIn("max-parallel: 12", workflow)
        self.assertIn("matrix: ${{ fromJSON(needs.prepare.outputs.matrix) }}", workflow)
        self.assertEqual(workflow.count("runtime/transport.py complete"), 1)
        self.assertEqual(workflow.count("runtime/transport.py diagnose"), 1)
        units = workflow.split("  units:\n", 1)[1].split("\n  aggregate:\n", 1)[0]
        self.assertNotIn("transport.py complete", units)
        self.assertNotIn("transport.py diagnose", units)
        self.assertIn("--no-persist-registry", units)


if __name__ == "__main__":
    unittest.main()
