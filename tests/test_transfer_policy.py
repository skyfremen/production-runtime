import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from test_schema import valid_request
from output.transfer import build_upload_body, expected_publication
from base.contract import marker_tag


class PublicationUploadPolicyTests(unittest.TestCase):
    def test_scheduled_payload_is_private_and_scheduled(self):
        request = valid_request()
        body = build_upload_body(request, require_future=False)
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertFalse(body["status"]["selfDeclaredMadeForKids"])
        self.assertEqual(body["status"]["publishAt"], request["publication"]["publish_at"])
        self.assertIn(marker_tag(request["content_id"]), body["snippet"]["tags"])

    def test_immediate_payload_is_public_without_publish_at(self):
        request = valid_request()
        request["publication"] = {
            "mode": "immediate",
            "timezone": "Asia/Singapore",
            "publish_at": None,
        }
        body = build_upload_body(request, require_future=False)
        self.assertEqual(body["status"]["privacyStatus"], "public")
        self.assertFalse(body["status"]["selfDeclaredMadeForKids"])
        self.assertNotIn("publishAt", body["status"])
        self.assertIsNone(expected_publication(request, require_future=False))

    def test_immediate_payload_rejects_publish_at(self):
        request = valid_request()
        request["publication"] = {
            "mode": "immediate",
            "timezone": "Asia/Singapore",
            "publish_at": "2099-09-09T16:00:00Z",
        }
        with self.assertRaisesRegex(ValueError, "publish_at=null"):
            build_upload_body(request, require_future=False)

    def test_missing_publication_is_rejected(self):
        request = valid_request()
        request.pop("publication")
        with self.assertRaisesRegex(ValueError, "Publication contract is required"):
            build_upload_body(request, require_future=False)

    def test_unknown_publication_mode_is_rejected(self):
        request = valid_request()
        request["publication"]["mode"] = "public"
        with self.assertRaisesRegex(ValueError, "scheduled or immediate"):
            expected_publication(request, require_future=False)

    def test_static_validation_can_check_elapsed_slot_without_authorizing_upload(self):
        now = datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc)
        request = valid_request()
        publish_at = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        request["publication"]["publish_at"] = publish_at
        body = build_upload_body(request, require_future=False, now_utc=now)
        self.assertEqual(body["status"]["publishAt"], publish_at)
        with self.assertRaisesRegex(ValueError, "future"):
            build_upload_body(request, now_utc=now)

    def test_description_limit_fails_before_upload(self):
        request = valid_request()
        request["youtube"]["description"] = "x" * 4995
        with self.assertRaisesRegex(ValueError, "5000-byte"):
            build_upload_body(request, require_future=False)

    def test_tag_limit_fails_before_upload(self):
        request = valid_request()
        request["youtube"]["hashtags"] = ["#" + (str(i) * 180) for i in range(3)]
        with self.assertRaisesRegex(ValueError, "500-character"):
            build_upload_body(request, require_future=False)


if __name__ == "__main__":
    unittest.main()
