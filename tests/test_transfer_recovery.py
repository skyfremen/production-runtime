import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from output import transfer as upload
from test_schema import valid_request
from base.contract import marker_tag

VIDEO_ID = "AbCdEfGhI12"
CHANNEL = {
    "id": "UC_TEST",
    "contentDetails": {"relatedPlaylists": {"uploads": "UU_TEST"}},
}


def intent_for(request):
    return {
        "created_at": "2099-09-09T15:50:00Z",
        "upload_body": upload.build_upload_body(request, require_future=False),
    }


def identity_for(request):
    return {
        "content_id": request["content_id"],
        "request_path": "request.json",
        "request_blob_sha": "a" * 40,
        "source_commit_sha": "b" * 40,
    }


def youtube_with_tags(tags, *, next_page=None, playlist_time="2099-09-09T15:51:00Z"):
    youtube = Mock()
    playlist = {
        "items": [
            {
                "contentDetails": {"videoId": VIDEO_ID},
                "snippet": {"publishedAt": playlist_time},
            }
        ]
    }
    if next_page:
        playlist["nextPageToken"] = next_page
    youtube.playlistItems.return_value.list.return_value.execute.return_value = playlist
    request = valid_request()
    body = upload.build_upload_body(request, require_future=False)
    youtube.videos.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "id": VIDEO_ID,
                "snippet": {
                    **body["snippet"],
                    "channelId": CHANNEL["id"],
                    "publishedAt": "2099-09-09T15:51:00Z",
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

    def test_post_intent_reconciliation_recovers_by_current_marker(self):
        request = valid_request()
        marker = marker_tag(request["content_id"])
        found = upload.find_after_intent(
            youtube_with_tags([marker, "Shorts"]),
            intent_for(request),
            identity_for(request),
            channel=CHANNEL,
        )

        self.assertEqual(found["id"], VIDEO_ID)
        self.assertNotIn("content_id=", found["snippet"]["description"])

    def test_post_intent_reconciliation_does_not_match_without_marker(self):
        request = valid_request()
        found = upload.find_after_intent(
            youtube_with_tags(["Shorts"]),
            intent_for(request),
            identity_for(request),
            channel=CHANNEL,
        )
        self.assertIsNone(found)

    def test_post_intent_reconciliation_stops_when_page_is_older_than_intent(self):
        request = valid_request()
        youtube = youtube_with_tags(
            ["Shorts"],
            next_page="unused",
            playlist_time="2099-09-09T15:40:00Z",
        )
        found = upload.find_after_intent(
            youtube,
            intent_for(request),
            identity_for(request),
            channel=CHANNEL,
        )
        self.assertIsNone(found)
        self.assertEqual(youtube.playlistItems.return_value.list.call_count, 1)

    def test_post_intent_limit_is_not_treated_as_absence(self):
        request = valid_request()
        youtube = youtube_with_tags(["Shorts"], next_page="more")
        with self.assertRaisesRegex(upload.RecoveryBlocked, "Bounded post-intent"):
            upload.find_after_intent(
                youtube,
                intent_for(request),
                identity_for(request),
                channel=CHANNEL,
                max_videos=1,
            )


if __name__ == "__main__":
    unittest.main()
