import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from resources import media, resolve


class BackgroundRecoveryTests(unittest.TestCase):
    asset = {
        "id": "px-37904635",
        "download_url": "https://videos.pexels.com/video-files/37904635/16083898_1080_1920_60fps.mp4",
    }
    segment = {"segment_start_seconds": 0.0, "segment_duration_seconds": 100.0}
    replacement = "https://videos.pexels.com/video-files/11496606/11496606-uhd_3840_2160_30fps.mp4"

    def test_retired_background_uses_explicit_approved_replacement(self):
        def preflight(url):
            return (url == self.replacement, "HTTP 206" if url == self.replacement else "HTTP 403/404", 0.0)
        with patch.object(media, "preflight", side_effect=preflight):
            result = resolve._resolve_one(self.asset, Path("unused.mp4"), self.segment, do_download=False, do_preflight=True)
        self.assertEqual(self.replacement, result["source_url_used"])
        self.assertEqual("px-11496606", result["replacement_asset_id"])
        self.assertTrue(result["generic_source_fallback_used"])
        self.assertFalse(result["pexels_original_fallback_used"])

    def test_working_original_keeps_original_background(self):
        with patch.object(media, "preflight", return_value=(True, "HTTP 206", 0.0)):
            result = resolve._resolve_one(self.asset, Path("unused.mp4"), self.segment, do_download=False, do_preflight=True)
        self.assertEqual(self.asset["download_url"], result["source_url_used"])
        self.assertIsNone(result.get("replacement_asset_id"))

    def test_replacement_cannot_bypass_frozen_segment_duration(self):
        def preflight(url):
            return (url == self.replacement, "HTTP 206" if url == self.replacement else "HTTP 403/404", 0.0)
        with tempfile.TemporaryDirectory() as directory, patch.object(media, "preflight", side_effect=preflight), patch.object(media, "download", return_value={}), patch.object(resolve, "_media_duration_seconds", return_value=50.0):
            with self.assertRaisesRegex(RuntimeError, "selected range exceeds media"):
                resolve._resolve_one(self.asset, Path(directory) / "short.mp4", self.segment, do_download=True, do_preflight=True)

    def test_unlisted_failed_background_still_fails(self):
        with patch.object(media, "preflight", return_value=(False, "HTTP 403", 0.0)):
            with self.assertRaisesRegex(RuntimeError, "no executable media source"):
                resolve._resolve_one({"id": "px-999", "download_url": "https://example.invalid/video.mp4"}, Path("unused.mp4"), self.segment, do_download=False, do_preflight=True)


if __name__ == "__main__":
    unittest.main()
