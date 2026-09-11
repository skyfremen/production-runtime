import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from resources.validate import validate_registry_data, validate_request_backgrounds
from test_schema import valid_request


def registry():
    def asset(n):
        return {
            "id": f"satisfying-{n:03}",
            "type": "video",
            "title": f"Asset {n}",
            "source": "Pexels",
            "source_page": f"https://www.pexels.com/video/{1000+n}/",
            "direct_url": f"https://www.pexels.com/download/video/{1000+n}/",
            "creator": None,
            "license": "Pexels License",
            "commercial_use": True,
            "attribution_required": False,
            "verified": True,
            "last_verified_at": "2026-09-09T00:00:00+08:00",
            "status": "active",
            "orientation": "unknown",
            "visual_tags": ["satisfying", "motion"],
            "motion_type": "continuous",
            "motion_intensity": "medium",
            "loopability_score": 90,
            "visual_satisfaction_score": 90,
            "caption_readability_score": 90,
            "has_embedded_text": False,
            "has_watermark": False,
            "renditions": [],
        }
    return {"schema_version": 3, "assets": [asset(1), asset(2)]}


class MediaRegistryTests(unittest.TestCase):
    def test_registry_valid(self):
        self.assertEqual(validate_registry_data(registry()), [])

    def test_usage_state_is_forbidden(self):
        data = registry()
        data["assets"][0]["usage_count"] = 1
        self.assertTrue(any("usage history" in x for x in validate_registry_data(data)))

    def test_request_ids_resolve(self):
        primary, backup = validate_request_backgrounds(valid_request(), registry())
        self.assertEqual(primary["id"], "satisfying-001")
        self.assertEqual(backup["id"], "satisfying-002")

    def test_registry_scales_beyond_999_logical_assets(self):
        data = registry()
        extra = dict(data["assets"][0])
        extra.update({
            "id": "satisfying-1000",
            "source_page": "https://www.pexels.com/video/9999999/",
            "direct_url": "https://www.pexels.com/download/video/9999999/",
        })
        data["assets"].append(extra)
        self.assertEqual(validate_registry_data(data), [])

    def test_deterministic_ai_sourced_pexels_id_is_valid(self):
        data = registry()
        sourced = dict(data["assets"][0])
        sourced.update({
            "id": "satisfying-px-424242",
            "provider_asset_id": "424242",
            "source_page": "https://www.pexels.com/video/sample-424242/",
            "direct_url": "https://www.pexels.com/download/video/424242/",
            "orientation": "vertical",
            "renditions": [{
                "id": "hd", "width": 1080, "height": 1920, "fps": 30,
                "file_type": "video/mp4", "direct_url": "https://videos.pexels.com/424242-hd.mp4",
            }],
        })
        data["assets"].append(sourced)
        self.assertEqual(validate_registry_data(data), [])

    def test_registry_rejects_4k_only_active_asset_for_production(self):
        data = registry()
        data["assets"][0]["renditions"] = [{
            "id": "4k", "width": 3840, "height": 2160, "fps": 30,
            "file_type": "video/mp4", "direct_url": "https://videos.pexels.com/4k-only.mp4",
        }]
        errors = validate_registry_data(data)
        self.assertTrue(any("<=1080p production rendition" in x for x in errors))


if __name__ == "__main__":
    unittest.main()
