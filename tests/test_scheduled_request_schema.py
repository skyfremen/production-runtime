import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from test_request_schema import valid_request
from publishing.upload import build_upload_body
from validation.validate_content import validate_request_data


class ScheduledRequestSchemaTests(unittest.TestCase):
    def test_scheduled_request_passes(self):
        self.assertEqual(validate_request_data(valid_request()), [])

    def test_requires_exact_singapore_schedule_contract(self):
        data = valid_request()
        data["publication"]["timezone"] = "UTC"
        self.assertTrue(
            any("Asia/Singapore" in error for error in validate_request_data(data))
        )

    def test_title_must_be_truthful_and_present_in_competition(self):
        data = valid_request()
        data["planning"]["title_candidates"][0]["truthful"] = False
        self.assertTrue(
            any("must be truthful" in error for error in validate_request_data(data))
        )

        data = valid_request()
        data["youtube"]["title"] = "A Different Accurate Title #Shorts"
        self.assertTrue(
            any("exactly match" in error for error in validate_request_data(data))
        )

    def test_rejects_uncontrolled_attribute(self):
        data = valid_request()
        data["planning"]["attributes"]["opening_style"] = "RANDOM_FREE_TEXT"
        self.assertTrue(
            any("opening_style" in error for error in validate_request_data(data))
        )

    def test_requires_semantic_backend_tags(self):
        data = valid_request()
        data["youtube"].pop("tags")
        self.assertTrue(
            any("youtube missing fields: tags" in error for error in validate_request_data(data))
        )

        data = valid_request()
        data["youtube"]["tags"] = ["one", "two", "three"]
        self.assertTrue(any("4-12" in error for error in validate_request_data(data)))

    def test_upload_body_contains_planned_tags_hashtags_and_hidden_marker(self):
        body = build_upload_body(valid_request(), require_future=False)
        tags = body["snippet"]["tags"]
        self.assertIn("wacky dramas", tags)
        self.assertIn("workplace drama", tags)
        self.assertIn("Shorts", tags)
        self.assertIn("WackyDramas", tags)
        self.assertTrue(tags[0].startswith("wd-id-"))
        self.assertEqual(len(tags), len({tag.lower() for tag in tags}))

    def test_description_gets_missing_hashtags_once(self):
        body = build_upload_body(valid_request(), require_future=False)
        description = body["snippet"]["description"]
        self.assertEqual(description.count("#WackyDramas"), 1)
        self.assertIn("Would you have confronted him?", description)

    def test_upload_body_is_private_with_exact_publish_at(self):
        data = valid_request()
        with patch("publishing.upload.datetime") as mocked_datetime:
            from datetime import datetime, timezone

            mocked_datetime.fromisoformat.side_effect = datetime.fromisoformat
            mocked_datetime.now.return_value = datetime(
                2099, 9, 9, 0, 0, tzinfo=timezone.utc
            )
            body = build_upload_body(data)
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertEqual(body["status"]["publishAt"], data["publication"]["publish_at"])


if __name__ == "__main__":
    unittest.main()

