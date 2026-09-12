import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core


class DiagnosticDetailTests(unittest.TestCase):
    def test_private_diagnostic_preserves_worker_prepare_root_cause(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workers = root / "workers"
            worker = workers / "00-wd-test"
            worker.mkdir(parents=True)
            (worker / "prepare.log").write_text(
                "$ python runtime/output/execute.py --stage prepare --request request.json\n"
                "RecoveryBlocked: Reconciliation index bootstrap is missing; fresh upload forbidden\n",
                encoding="utf-8",
            )
            internal = root / "internal.log"
            internal.write_text("pipeline reported a per-video prepare failure\n", encoding="utf-8")
            diagnostic = root / "diagnostic.json"
            classification = root / "classification.json"

            with mock.patch.object(core, "WORKER_ROOT", workers), \
                 mock.patch.object(core, "INTERNAL_LOG", internal), \
                 mock.patch.object(core, "DIAGNOSTIC", diagnostic), \
                 mock.patch.object(core, "FAILURE_CLASSIFICATION", classification):
                try:
                    raise core.RunnerError("One or more items failed")
                except core.RunnerError as exc:
                    core.record_failure(exc)

            payload = json.loads(diagnostic.read_text(encoding="utf-8"))
            detail = payload["detail"]
            self.assertIn("Worker logs:", detail)
            self.assertIn("prepare.log", detail)
            self.assertIn(
                "Reconciliation index bootstrap is missing; fresh upload forbidden",
                detail,
            )
            self.assertEqual(payload["stage"], "execute")
            self.assertTrue(payload["retryable"])


if __name__ == "__main__":
    unittest.main()
