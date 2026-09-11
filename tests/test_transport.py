import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from transport import PrivateState, TransportError, git_blob_sha, refresh_registry


class TransportConcurrencyTests(unittest.TestCase):
    def test_immutable_create_retries_unrelated_branch_conflict(self):
        state = object.__new__(PrivateState)
        state.current_content = Mock(side_effect=[
            TransportError("missing"), TransportError("missing"), TransportError("missing")
        ])
        state.api = Mock(side_effect=[TransportError("conflict"), {}])
        with patch("transport.time.sleep"):
            created = state.create("opaque/result.json", {"value": 1}, "message")
        self.assertTrue(created)
        self.assertEqual(state.api.call_count, 2)

    def test_refresh_requires_exact_prepared_registry_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.json"
            registry.write_text("old")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"registry": str(registry)}))
            raw = b'{"schema_version":3}\n'
            sha = git_blob_sha(raw)
            state = Mock()
            state.current_content.return_value = (raw, sha)
            with patch("transport.PrivateState", return_value=state):
                refresh_registry(manifest, sha)
            self.assertEqual(registry.read_bytes(), raw)
            with patch("transport.PrivateState", return_value=state):
                with self.assertRaises(TransportError):
                    refresh_registry(manifest, "a" * 40)


if __name__ == "__main__":
    unittest.main()
