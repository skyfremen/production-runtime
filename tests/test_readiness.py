import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from errors import E_AUTH, E_PREPARE, E_RESOURCE
from guard import readiness
from output.state import RecoveryBlocked, index_bootstrap_path


class ReadinessTests(unittest.TestCase):
    def manifest(self, sourcing=None):
        return {
            "schema_version": 1,
            "batch_id": "b_" + "a" * 30,
            "source_sha": "b" * 40,
            "requests": ["runtime/content/requests/wd-test.json"],
            "planning": [],
            "sourcing": list(sourcing or []),
            "request_sources": {},
            "registry": "runtime/media-library/backgrounds.json",
            "registry_source_blob_sha": "c" * 40,
        }

    def required_env(self):
        return {
            "PRIVATE_STATE_TOKEN": "state-token",
            "PRIVATE_STATE_REPOSITORY": "owner/repo",
            "RUNTIME_AUTH_A": "client-id",
            "RUNTIME_AUTH_B": "client-secret",
            "RUNTIME_AUTH_C": "refresh-token",
        }

    def test_environment_does_not_require_source_key_when_sourcing_unused(self):
        with mock.patch.dict(os.environ, self.required_env(), clear=True):
            readiness.check_environment(self.manifest())

    def test_environment_requires_source_key_only_when_sourcing_is_present(self):
        with mock.patch.dict(os.environ, self.required_env(), clear=True):
            with self.assertRaises(readiness.ReadinessError) as caught:
                readiness.check_environment(
                    self.manifest(["runtime/content/background-sourcing/day.json"])
                )
        self.assertEqual(caught.exception.code, E_RESOURCE)
        self.assertFalse(caught.exception.retryable)

    def test_missing_remote_credentials_fail_non_retryable(self):
        env = self.required_env()
        env["RUNTIME_AUTH_C"] = ""
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(readiness.ReadinessError) as caught:
                readiness.check_environment(self.manifest())
        self.assertEqual(caught.exception.code, E_AUTH)
        self.assertFalse(caught.exception.retryable)

    @mock.patch("guard.readiness.make_client", side_effect=KeyError("RUNTIME_AUTH_A"))
    def test_remote_missing_credentials_is_non_retryable(self, _client):
        with self.assertRaises(readiness.ReadinessError) as caught:
            readiness.check_remote_channel()
        self.assertEqual(caught.exception.code, E_AUTH)
        self.assertFalse(caught.exception.retryable)

    @mock.patch(
        "guard.readiness.authenticated_channel",
        side_effect=RecoveryBlocked("wrong channel"),
    )
    @mock.patch("guard.readiness.make_client", return_value=object())
    def test_wrong_remote_identity_is_non_retryable(self, _client, _channel):
        with self.assertRaises(readiness.ReadinessError) as caught:
            readiness.check_remote_channel()
        self.assertEqual(caught.exception.code, E_AUTH)
        self.assertFalse(caught.exception.retryable)

    @mock.patch("guard.readiness.GitHubState")
    def test_reconciliation_index_valid_marker_passes(self, state):
        state.return_value.load.return_value = SimpleNamespace(data={
            "schema_version": 1,
            "status": "complete",
            "conflicts": 0,
        })
        readiness.check_reconciliation_index()
        state.return_value.load.assert_called_once_with(index_bootstrap_path())

    @mock.patch("guard.readiness.GitHubState")
    def test_reconciliation_index_missing_is_non_retryable(self, state):
        state.return_value.load.return_value = None
        with self.assertRaises(readiness.ReadinessError) as caught:
            readiness.check_reconciliation_index()
        self.assertEqual(caught.exception.code, E_PREPARE)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.check, "reconciliation_index_missing")

    @mock.patch("guard.readiness.GitHubState")
    def test_reconciliation_index_invalid_is_non_retryable(self, state):
        state.return_value.load.return_value = SimpleNamespace(data={
            "schema_version": 1,
            "status": "incomplete",
            "conflicts": 0,
        })
        with self.assertRaises(readiness.ReadinessError) as caught:
            readiness.check_reconciliation_index()
        self.assertEqual(caught.exception.code, E_PREPARE)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.check, "reconciliation_index_invalid")

    @mock.patch("guard.readiness.GitHubState")
    def test_reconciliation_index_read_failure_is_retryable(self, state):
        state.return_value.load.side_effect = RecoveryBlocked("private state unavailable")
        with self.assertRaises(readiness.ReadinessError) as caught:
            readiness.check_reconciliation_index()
        self.assertEqual(caught.exception.code, E_PREPARE)
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.check, "reconciliation_index_unavailable")

    def test_shared_runner_calls_all_global_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            manifest_path.write_text(json.dumps(self.manifest()), encoding="utf-8")
            with mock.patch("guard.readiness.check_environment") as environment, \
                 mock.patch("guard.readiness.check_reconciliation_index") as reconciliation, \
                 mock.patch("guard.readiness.check_runtime_dependencies") as runtime, \
                 mock.patch("guard.readiness.check_filesystem") as filesystem, \
                 mock.patch("guard.readiness.check_registry") as registry, \
                 mock.patch("guard.readiness.check_remote_channel") as remote:
                readiness.run_readiness(manifest_path)
            environment.assert_called_once()
            reconciliation.assert_called_once_with()
            runtime.assert_called_once_with()
            filesystem.assert_called_once_with()
            registry.assert_called_once()
            remote.assert_called_once_with()

    def test_diagnostic_marks_failure_before_expensive_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "diagnostic.json"
            error = readiness.ReadinessError(E_AUTH, True, "auth")
            with mock.patch.object(readiness, "DIAGNOSTIC", target):
                readiness.write_diagnostic(error)
            payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["stage"], "prepare")
        self.assertEqual(payload["error_code"], E_AUTH)
        self.assertTrue(payload["retryable"])
        self.assertFalse(payload["execution_started"])
        self.assertFalse(payload["verification_completed"])

    def test_readiness_has_no_upload_or_fresh_upload_authorization_path(self):
        source = Path(readiness.__file__).read_text(encoding="utf-8")
        self.assertNotIn("videos().insert", source)
        self.assertNotIn("execute_upload", source)
        self.assertNotIn("require_index_ready", source)
        self.assertNotIn("authorize_fresh_upload", source)

    def test_workflow_ordering_keeps_global_gate_before_fanout_and_execute(self):
        repo = Path(__file__).resolve().parents[1]
        daily = (repo / ".github/workflows/run.yml").read_text(encoding="utf-8")
        single = (repo / ".github/workflows/single.yml").read_text(encoding="utf-8")
        self.assertLess(daily.index("name: Readiness"), daily.index("name: Plan"))
        self.assertIn("fail-fast: false", daily)
        self.assertLess(single.index("name: Readiness"), single.index("name: Execute"))


if __name__ == "__main__":
    unittest.main()
