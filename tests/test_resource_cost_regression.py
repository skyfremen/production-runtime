import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from resources import resolve as media_resolver


def rendition(name, width, height, fps=30, size=None):
    value = {
        "id": name,
        "width": width,
        "height": height,
        "fps": fps,
        "file_type": "video/mp4",
        "quality": "hd",
        "direct_url": f"https://videos.pexels.com/{name}.mp4",
    }
    if size is not None:
        value["file_size_bytes"] = size
    return value


def asset(renditions):
    return {
        "id": "satisfying-001",
        "caption_readability_score": 90,
        "renditions": list(renditions),
    }


class ResourceCostRegressionTests(unittest.TestCase):
    def test_equal_geometry_uses_smaller_reliable_file(self):
        item = asset([
            rendition("large-file", 1080, 1920, 30, 25_000_000),
            rendition("small-file", 1080, 1920, 30, 11_000_000),
        ])
        selected = media_resolver.select_best_rendition(item)
        self.assertEqual(selected["id"], "small-file")

    def test_smallest_sufficient_landscape_wins_over_larger_uhd(self):
        # 3264x1836 leaves a 1032.75x1836 portrait crop, requiring only ~1.046x
        # enlargement and therefore satisfying the current 1.05 bounded-upscale floor.
        item = asset([
            rendition("uhd", 3840, 2160, 30, 36_000_000),
            rendition("sufficient", 3264, 1836, 30, 22_000_000),
            rendition("insufficient", 3200, 1800, 30, 20_000_000),
        ])
        selected = media_resolver.select_best_rendition(item)
        self.assertEqual(selected["id"], "sufficient")

    def test_normalized_physical_cache_hit_avoids_network_and_normalization(self):
        logical = asset([rendition("r1", 1080, 1920, 30, 12_000_000)])
        physical = logical["renditions"][0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            target = root / "background.asset"
            identity = json.dumps(
                [logical["id"], physical["id"], physical["direct_url"], 1080, 1920, 30],
                separators=(",", ":"),
            )
            key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            (cache / f"{key}.mp4").write_bytes(b"c" * 12000)
            (cache / f"{key}.json").write_text(
                json.dumps({
                    "downloaded_bytes": 12_000_000,
                    "background_normalization_applied": True,
                    "background_normalization_duration_seconds": 1.25,
                    "render_probe": {"codec": "h264", "width": 1080, "height": 1920, "fps": 30.0},
                }),
                encoding="utf-8",
            )
            exact_probe = {"codec": "h264", "width": 1080, "height": 1920, "fps": 30.0}
            with mock.patch.dict(os.environ, {"RUNTIME_RESOURCE_CACHE": str(cache)}), \
                 mock.patch.object(media_resolver, "probe_video", return_value=exact_probe), \
                 mock.patch.object(media_resolver.urllib.request, "urlopen", side_effect=AssertionError("network must not be used")), \
                 mock.patch.object(media_resolver, "normalize_for_render", side_effect=AssertionError("cache hit must not normalize")):
                metrics = media_resolver.download(logical, physical, target)

            self.assertTrue(metrics["background_cache_hit"])
            self.assertEqual(metrics["background_cache_key"], key)
            self.assertEqual(target.stat().st_size, 12000)
            self.assertEqual(metrics["render_background_bytes"], 12000)


if __name__ == "__main__":
    unittest.main()
