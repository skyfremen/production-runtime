import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from output import transfer as upload
from test_schema import valid_request
from base.contract import marker_tag

VIDEO_ID = "AbCdEfGhI12"
CHANNEL = {
    "id": "UC_TEST",
    "contentDetails": {"relatedPlaylists": {"uploads": "UU_TEST"}},
}


def youtube_with_tags(tags):
    youtube = Mock()
    youtube.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"videoId": VIDEO_ID}}]
    }
    youtube.videos.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "id": VIDEO_ID,
                "snippet": {
                    "channelId": CHANNEL["id"],
                    "publishedAt": "2099-09-09T15:51:00Z",
                    "description": "Viewer-facing description only",
                    "tags": tags,
                },
                "status": {"privacyStatus": "private"},
            }
        ]
    }
    return youtube


class TagRecoveryTests(unittest.TestCase):
    def test_upload_body_keeps_recovery_out_of_description_and_in_tags(self):
        request = valid_request()
        body = upload.build_upload_body(request, require_future=False)
        marker = marker_tag(request["content_id"])

        self.assertNotIn("content_id=", body["snippet"]["description"])
        self.assertNotIn("request_blob_sha=", body["snippet"]["description"])
        self.assertIn(marker, body["snippet"]["tags"])
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertEqual(body["status"]["publishAt"], request["publication"]["publish_at"])

    def test_find_existing_recovers_by_current_marker(self):
        request = valid_request()
        marker = marker_tag(request["content_id"])
        found = upload.find_existing_by_marker(
            youtube_with_tags([marker, "Shorts"]),
            request["content_id"],
            channel=CHANNEL,
        )

        self.assertEqual(found["id"], VIDEO_ID)
        self.assertNotIn("content_id=", found["snippet"]["description"])

    def test_find_existing_does_not_match_without_marker(self):
        request = valid_request()
        found = upload.find_existing_by_marker(
            youtube_with_tags(["Shorts"]),
            request["content_id"],
            channel=CHANNEL,
        )
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
