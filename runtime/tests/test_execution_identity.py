import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import core
from engine.pipeline import ProductionPipeline


class ExecutionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.identity = {
            "execution_id": "ex-" + "a" * 24,
            "content_id": "wd-" + "b" * 24,
            "request_id": "rq-" + "c" * 24,
            "request_path": "content/requests/rq-" + "c" * 24 + ".json",
            "request_source_sha": "d" * 40,
            "request_blob_sha": "e" * 40,
            "item_blob_sha": "f" * 40,
        }

    def test_pipeline_exports_canonical_identity_and_uses_item_blob_locally(self):
        request = "runtime/content/requests/wd-" + "b" * 24 + ".json"
        pipeline = ProductionPipeline(
            base_env={
                "REQUEST_SOURCE_MAP_JSON": json.dumps({request: self.identity})
            }
        )

        env = pipeline._env(request)

        self.assertEqual(self.identity["request_source_sha"], env["SOURCE_COMMIT_SHA"])
        self.assertEqual(self.identity["item_blob_sha"], env["SOURCE_REQUEST_BLOB_SHA"])
        self.assertEqual(self.identity, json.loads(env["REQUEST_IDENTITY_JSON"]))

    def test_core_rejects_manifest_v1(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(
                json.dumps({"manifest_version": 1, "requests": ["request.json"]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "manifest v2"):
                core.run(manifest)

    def test_core_accepts_manifest_v2_and_propagates_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = root / "request.json"
            registry = root / "registry.json"
            manifest = root / "manifest.json"
            request.write_text("{}", encoding="utf-8")
            registry.write_text('{"assets": []}', encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "manifest_version": 2,
                        "execution_id": self.identity["execution_id"],
                        "requests": [str(request)],
                        "registry": str(registry),
                        "request_sources": {str(request): self.identity},
                    }
                ),
                encoding="utf-8",
            )
            pipeline = MagicMock()
            pipeline.run.return_value = {"failed": 0}
            with patch.object(core, "validate_request_data"), patch.object(
                core, "load_registry", return_value={"assets": []}
            ), patch.object(core, "validate_request_backgrounds"), patch.object(
                core, "run_preflight"
            ), patch.object(
                core, "ProductionPipeline", return_value=pipeline
            ) as pipeline_factory, patch.object(
                core, "run_remote_verification"
            ), patch.object(core, "run_cmd"), patch.object(core.Path, "write_text"):
                core.run(manifest)

        base_env = pipeline_factory.call_args.kwargs["base_env"]
        self.assertEqual(
            {str(request): self.identity},
            json.loads(base_env["REQUEST_SOURCE_MAP_JSON"]),
        )


if __name__ == "__main__":
    unittest.main()
