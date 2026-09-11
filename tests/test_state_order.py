import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from output.execute import prepare
from output.state import blob_sha, encoded_json, record_path
from test_state import CHANNEL, MemoryState, fixture


class RecoveryScheduleOrderTests(unittest.TestCase):
    def test_past_scheduled_request_with_durable_upload_recovers_instead_of_fresh_skip(self):
        request, identity, record, _item = fixture()
        request["publication"] = {
            "mode": "scheduled",
            "timezone": "Asia/Singapore",
            "publish_at": "2020-01-01T00:00:00Z",
        }
        state = MemoryState()
        state.records[record_path(identity["content_id"], "upload")] = record

        with patch("output.execute.restore_upload") as restore:
            upload_required = prepare(request, identity, state, Mock(), CHANNEL, False)

        self.assertFalse(upload_required)
        restore.assert_called_once()
        stored = restore.call_args.args[0]
        self.assertEqual(stored.data["youtube_video_id"], record["youtube_video_id"])
        self.assertEqual(stored.sha, blob_sha(encoded_json(record)))


if __name__ == "__main__":
    unittest.main()
