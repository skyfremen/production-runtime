import json
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from resources import resolve as media_resolver
from resources.policy import crop_fill_geometry, rendition_is_production_suitable


def rendition(name, width, height, fps=30, size=None, url=None):
    item = {
        "id": name, "width": width, "height": height, "fps": fps,
        "file_type": "video/mp4", "quality": "hd",
        "direct_url": url or f"https://videos.pexels.com/{name}.mp4",
    }
    if size:
        item["file_size_bytes"] = size
    return item


def asset(n, renditions=None):
    return {
        "id": f"satisfying-{n:03}", "type": "video", "title": f"Asset {n}",
        "source": "Pexels", "source_page": f"https://www.pexels.com/video/{1000+n}/",
        "direct_url": f"https://www.pexels.com/download/video/{1000+n}/",
        "provider_asset_id": str(1000+n), "creator": "Creator", "license": "Pexels License",
        "commercial_use": True, "attribution_required": False, "verified": True,
        "last_verified_at": "2026-09-09T00:00:00+00:00", "status": "active",
        "orientation": "vertical", "visual_tags": ["satisfying", "fluid"],
        "motion_type": "loop", "motion_intensity": "medium", "loopability_score": 90,
        "visual_satisfaction_score": 90, "caption_readability_score": 90,
        "has_embedded_text": False, "has_watermark": False,
        "renditions": list(renditions or []),
    }


def request():
    return {"visual": {"background_primary_id": "satisfying-001", "background_backup_id": "satisfying-002"}}


class RenditionSelectionTests(unittest.TestCase):
    def test_portrait_selects_1080_not_insufficient_smaller_files(self):
        item = asset(1, [
            rendition("r540", 540, 960), rendition("r1080", 1080, 1920),
            rendition("r240", 240, 426),
        ])
        self.assertEqual(media_resolver.select_best_rendition(item)["id"], "r1080")

    def test_weak_vertical_source_is_rejected(self):
        item = asset(1, [rendition("r1080", 1080, 1920), rendition("r540", 540, 960)])
        self.assertEqual(media_resolver.select_best_rendition(item)["id"], "r1080")

    def test_smallest_sufficient_area_is_preferred(self):
        item = asset(1, [rendition("r4k", 2160, 3840), rendition("r1080", 1080, 1920)])
        self.assertEqual(media_resolver.select_best_rendition(item)["id"], "r1080")

    def test_landscape_is_evaluated_after_vertical_crop(self):
        hd720 = rendition("landscape-720", 1280, 720)
        full_hd = rendition("landscape-hd", 1920, 1080)
        qhd = rendition("landscape-qhd", 2560, 1440)
        four_k = rendition("landscape-4k", 3840, 2160)
        self.assertFalse(rendition_is_production_suitable(hd720))
        self.assertFalse(rendition_is_production_suitable(full_hd))
        self.assertFalse(rendition_is_production_suitable(qhd))
        self.assertTrue(rendition_is_production_suitable(four_k))
        geometry = crop_fill_geometry(1920, 1080)
        self.assertAlmostEqual(geometry["effective_crop_width"], 607.5, places=1)
        self.assertAlmostEqual(geometry["scale_factor"], 1920 / 1080, places=3)

    def test_horizontal_4k_is_selected_only_when_lower_renditions_fail_post_crop(self):
        item = asset(1, [
            rendition("4k", 3840, 2160, 30, 36_000_000),
            rendition("qhd", 2560, 1440, 30, 21_000_000),
            rendition("fhd", 1920, 1080, 30, 12_000_000),
            rendition("hd", 1280, 720, 30, 6_000_000),
        ])
        selected = media_resolver.select_best_rendition(item)
        self.assertEqual(selected["id"], "4k")

    def test_30fps_is_preferred_over_equivalent_60fps(self):
        item = asset(1, [rendition("r60", 1080, 1920, 60), rendition("r30", 1080, 1920, 30)])
        self.assertEqual(media_resolver.select_best_rendition(item)["id"], "r30")

    def test_insufficient_source_is_rejected(self):
        self.assertIsNone(media_resolver.select_best_rendition(asset(1, [rendition("small", 719, 1279)])))

    def test_generic_original_fallback_refuses_unknown_or_oversized_original(self):
        unknown = asset(1)
        oversized = asset(2)
        oversized.update({"width": 7680, "height": 4320, "fps": 30})
        bounded = asset(3)
        bounded.update({"width": 1080, "height": 1920, "fps": 30})
        self.assertIsNone(media_resolver.generic_fallback(unknown))
        self.assertIsNone(media_resolver.generic_fallback(oversized))
        self.assertIsNotNone(media_resolver.generic_fallback(bounded))

    def test_render_ready_h264_is_not_normalized_again(self):
        probe = {"codec": "h264", "width": 1080, "height": 1920, "fps": 29.97}
        self.assertFalse(media_resolver.normalization_required(probe))

    def test_oversized_or_wrong_codec_background_is_normalized(self):
        oversized = {"codec": "h264", "width": 3840, "height": 2160, "fps": 30}
        wrong_codec = {"codec": "vp9", "width": 1080, "height": 1920, "fps": 30}
        self.assertTrue(media_resolver.normalization_required(oversized))
        self.assertTrue(media_resolver.normalization_required(wrong_codec))

    def test_normalization_produces_exact_ephemeral_render_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "background.asset"
            target.write_bytes(b"s" * 12000)
            source_probe = {"codec": "h264", "width": 3840, "height": 2160, "fps": 29.97}
            output_probe = {"codec": "h264", "width": 1080, "height": 1920, "fps": 30.0}

            def fake_run(command, **_kwargs):
                Path(command[-1]).write_bytes(b"n" * 12000)
                return mock.MagicMock(returncode=0, stderr="")

            with mock.patch.object(media_resolver.subprocess, "run", side_effect=fake_run) as runner, \
                 mock.patch.object(media_resolver, "probe_video", return_value=output_probe):
                metrics = media_resolver.normalize_for_render(target, source_probe)

            self.assertTrue(metrics["background_normalization_applied"])
            self.assertEqual(metrics["render_probe"], output_probe)
            self.assertEqual(target.read_bytes(), b"n" * 12000)
            command = runner.call_args.args[0]
            self.assertIn("libx264", command)
            self.assertIn("ultrafast", command)
            self.assertIn("fps=30,scale=1080:1920", command[command.index("-vf") + 1])


class HttpTransportTests(unittest.TestCase):
    def test_preflight_uses_bounded_range_request_without_curl(self):
        headers = Message()
        headers["Content-Type"] = "video/mp4"
        response = mock.MagicMock(status=206, headers=headers)
        response.__enter__.return_value = response
        response.read.return_value = b"x"
        with mock.patch.object(media_resolver.urllib.request, "urlopen", return_value=response) as opener:
            ok, detail, _elapsed = media_resolver.preflight("https://example.test/video.mp4")
        self.assertTrue(ok)
        self.assertIn("video/mp4", detail)
        request_value = opener.call_args.args[0]
        self.assertEqual(request_value.get_header("Range"), "bytes=0-0")
        response.read.assert_called_once_with(1)


class ResolverFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.request_path = self.root / "request.json"
        self.registry_path = self.root / "backgrounds.json"
        self.request_path.write_text(json.dumps(request()), encoding="utf-8")
        self.output = self.root / "output"

    def tearDown(self):
        self.tmp.cleanup()

    def write_registry(self, assets):
        data = {"schema_version": 3, "assets": assets}
        self.registry_path.write_text(json.dumps(data), encoding="utf-8")

    @staticmethod
    def ok_preflight(_url):
        return True, "HTTP 206, video/mp4", 0.01

    @staticmethod
    def download_ok(_asset, rendition_value, _target, _width, _height, _duration=175):
        return {
            "downloaded_bytes": 123456, "download_duration_seconds": 0.5,
            "source_probe": {"width": rendition_value["width"], "height": rendition_value["height"],
                             "fps": rendition_value["fps"], "codec": "h264"},
        }

    def resolve(self, download_side_effect=None, preflight_side_effect=None):
        with mock.patch.object(media_resolver, "OUTPUT_DIR", self.output), \
             mock.patch.object(media_resolver, "preflight", side_effect=preflight_side_effect or self.ok_preflight), \
             mock.patch.object(media_resolver, "download", side_effect=download_side_effect or self.download_ok):
            return media_resolver.resolve(self.request_path, self.registry_path)

    def test_failed_best_uses_next_rendition_same_logical_asset(self):
        self.write_registry([
            asset(1, [rendition("best", 1080, 1920), rendition("next", 2160, 3840)]),
            asset(2, [rendition("backup", 1080, 1920)]),
        ])
        def downloader(asset_value, rendition_value, target, width, height, duration):
            if rendition_value["id"] == "best":
                raise RuntimeError("simulated failure")
            return self.download_ok(asset_value, rendition_value, target, width, height, duration)
        result = self.resolve(download_side_effect=downloader)
        self.assertEqual(result["background_asset_id"], "satisfying-001")
        self.assertEqual(result["rendition"]["id"], "next")
        self.assertTrue(result["rendition_fallback_used"])
        self.assertFalse(result["generic_source_fallback_used"])

    def test_all_primary_renditions_fail_then_logical_backup(self):
        self.write_registry([
            asset(1, [rendition("primary", 1080, 1920)]),
            asset(2, [rendition("backup", 1080, 1920)]),
        ])
        def downloader(asset_value, rendition_value, target, width, height, duration):
            if asset_value["id"] == "satisfying-001":
                raise RuntimeError("primary failed")
            return self.download_ok(asset_value, rendition_value, target, width, height, duration)
        result = self.resolve(download_side_effect=downloader)
        self.assertEqual(result["background_asset_id"], "satisfying-002")
        self.assertEqual(result["background_selection"], "backup")
        self.assertTrue(result["logical_fallback_used"])

    def test_never_substitutes_unrequested_third_asset(self):
        self.write_registry([
            asset(1, [rendition("primary", 1080, 1920)]),
            asset(2, [rendition("backup", 1080, 1920)]),
            asset(3, [rendition("third", 1080, 1920)]),
        ])
        attempted = []
        def failing_preflight(url):
            attempted.append(url)
            return False, "failed", 0.01
        with self.assertRaises(RuntimeError):
            self.resolve(preflight_side_effect=failing_preflight)
        self.assertEqual(len(attempted), 2)
        self.assertFalse(any("third" in url or "1003" in url for url in attempted))

    def test_runtime_metadata_records_logical_and_physical_selection(self):
        self.write_registry([
            asset(1, [rendition("physical-1", 1080, 1920, 30, 999999)]),
            asset(2, [rendition("physical-2", 1080, 1920)]),
        ])
        result = self.resolve()
        stored = json.loads((self.output / "background_selection.json").read_text())
        self.assertEqual(stored, result)
        self.assertEqual(stored["requested_primary_id"], "satisfying-001")
        self.assertEqual(stored["rendition"]["id"], "physical-1")
        self.assertEqual(stored["rendition"]["width"], 1080)
        self.assertEqual(stored["target"], {"width": 1080, "height": 1920, "fps": 30})
        self.assertEqual(stored["metrics"]["downloaded_bytes"], 123456)


if __name__ == "__main__":
    unittest.main()
