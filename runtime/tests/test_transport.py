import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import transport
from base.compat import contract_hash


class TransportSourceTests(unittest.TestCase):
    def test_background_registry_is_fetched_from_request_revision(self):
        execution_id = "ex-" + "a" * 24
        content_id = "wd-" + "b" * 24
        request_id = "rq-" + "c" * 24
        execution_source = "d" * 40
        request_source = "e" * 40
        item = {"content_id": content_id}
        batch = {"request_version": 2, "request_id": request_id, "items": [item]}
        batch_raw = transport.encoded_json(batch)
        item_raw = transport.encoded_json(item)
        execution = {
            "execution_version": 2,
            "execution_id": execution_id,
            "state": "prepared",
            "contract_hash": contract_hash(),
            "dispatch_id": "dp-" + "f" * 20,
            "content_id": content_id,
            "request_id": request_id,
            "request_path": f"content/requests/{request_id}.json",
            "request_source_sha": request_source,
            "request_blob_sha": transport.git_blob_sha(batch_raw),
            "item_blob_sha": transport.git_blob_sha(item_raw),
        }

        class FakeState:
            calls = []

            def read(self, path, ref):
                self.calls.append((path, ref))
                if path.startswith("content/executions/"):
                    return transport.encoded_json(execution)
                if path.startswith("content/requests/"):
                    return batch_raw
                return transport.encoded_json({"assets": []})

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            transport, "PrivateState", FakeState
        ), patch("transport.Path.write_bytes"), patch("transport.Path.write_text"):
            transport.fetch_execution(execution_id, execution_source, Path(tmp) / "manifest.json")

        self.assertIn(("data/backgrounds.json", request_source), FakeState.calls)
        self.assertNotIn(("data/backgrounds.json", execution_source), FakeState.calls)


if __name__ == "__main__":
    unittest.main()
