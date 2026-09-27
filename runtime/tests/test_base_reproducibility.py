import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class BaseReproducibilityTests(unittest.TestCase):
    def test_runtime_base_and_direct_dependencies_are_exactly_pinned(self):
        dockerfile = (ROOT / "base" / "Dockerfile").read_text(encoding="utf-8")
        dependencies = [
            line.strip()
            for line in (ROOT / "base" / "dependencies.txt").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        self.assertRegex(
            dockerfile.splitlines()[0],
            r"^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$",
        )
        self.assertTrue(dependencies)
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9_.-]+==[^<>=!~]+", item) for item in dependencies))
        self.assertNotIn("python -m spacy download", dockerfile)

    def test_transitive_dependencies_are_constrained_for_both_install_steps(self):
        dockerfile = (ROOT / "base" / "Dockerfile").read_text(encoding="utf-8")
        constraints_path = ROOT / "base" / "constraints.txt"
        self.assertTrue(constraints_path.is_file())
        constraints = [
            line.strip()
            for line in constraints_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        self.assertGreater(len(constraints), 50)
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9_.-]+==[^<>=!~]+", item) for item in constraints))
        self.assertIn("COPY base/constraints.txt /tmp/constraints.txt", dockerfile)
        self.assertEqual(dockerfile.count("--constraint /tmp/constraints.txt"), 2)

    def test_downloaded_models_have_verified_sha256_digests(self):
        dockerfile = (ROOT / "base" / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn(
            "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
            dockerfile,
        )
        self.assertIn(
            "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
            dockerfile,
        )
        self.assertIn(
            "1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85",
            dockerfile,
        )
        self.assertIn("f3ff3571791e39611d31c381e3a41a3af07b4987", dockerfile)
        self.assertIn("snapshot_download", dockerfile)
        self.assertIn("hashlib.sha256", dockerfile)

    def test_verify_workflow_runs_unit_tests_for_base_changes(self):
        workflow = (ROOT / ".github" / "workflows" / "verify.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("'base/**'", workflow)
        self.assertIn("python -m unittest discover -s runtime/tests", workflow)


if __name__ == "__main__":
    unittest.main()
