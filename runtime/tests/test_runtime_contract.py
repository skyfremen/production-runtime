import ast
import inspect
import unittest
from pathlib import Path
from unittest.mock import patch

from base import contract
from resources import media, resolve
from transform import verify


COMPOSE_PATH = Path(__file__).resolve().parents[1] / "transform" / "compose.py"


def imports_from_base_contract(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "base.contract"
        for alias in node.names
    }


class RuntimeContractTests(unittest.TestCase):
    def test_stale_targets_are_removed_and_hard_ceiling_is_unchanged(self):
        self.assertFalse(hasattr(contract, "PRODUCTION_TARGET_MIN_SECONDS"))
        self.assertFalse(hasattr(contract, "PRODUCTION_TARGET_MAX_SECONDS"))
        self.assertEqual(178.0, contract.PRODUCTION_MAX_SECONDS)
        self.assertEqual(0.10, contract.PRODUCTION_ENCODE_SAFETY_SECONDS)

    def test_shared_technical_thresholds_are_used_by_compose_and_verify(self):
        self.assertEqual(0.75, contract.BLACKDETECT_MAX_ALLOWED_SECONDS)
        self.assertEqual(5.0, contract.DEFAULT_TEST_RENDER_MAX_SECONDS)
        self.assertIs(
            contract.BLACKDETECT_MAX_ALLOWED_SECONDS,
            verify.BLACKDETECT_MAX_ALLOWED_SECONDS,
        )
        compose_imports = imports_from_base_contract(COMPOSE_PATH)
        self.assertIn("BLACKDETECT_MAX_ALLOWED_SECONDS", compose_imports)
        self.assertIn("DEFAULT_TEST_RENDER_MAX_SECONDS", compose_imports)
        compose_source = COMPOSE_PATH.read_text(encoding="utf-8")
        verify_source = Path(verify.__file__).read_text(encoding="utf-8")
        self.assertIn('os.getenv("STORY_RENDER_MAX_SECONDS", str(DEFAULT_TEST_RENDER_MAX_SECONDS))', compose_source)
        self.assertIn('os.getenv("STORY_RENDER_MAX_SECONDS", str(DEFAULT_TEST_RENDER_MAX_SECONDS))', verify_source)

    def test_background_segment_duration_is_required_keyword_only(self):
        parameter = inspect.signature(media.download).parameters["segment_duration_seconds"]
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, parameter.kind)
        self.assertIs(inspect.Parameter.empty, parameter.default)

    def test_production_resolver_passes_selected_segment_duration(self):
        asset = {"id": "px-1", "download_url": "https://example.invalid/video.mp4"}
        segment = {"segment_start_seconds": 0.0, "segment_duration_seconds": 7.25}
        with patch.object(media, "download", return_value={}) as download, patch.object(
            resolve, "_media_duration_seconds", return_value=10.0
        ):
            resolve._resolve_one(
                asset,
                Path("video.mp4"),
                segment,
                do_download=True,
                do_preflight=False,
            )
        self.assertEqual(7.25, download.call_args.kwargs["segment_duration_seconds"])

    def test_black_detection_boundary_is_strict(self):
        self.assertEqual(
            0.749999,
            verify.inline_blackdetect_max(
                {
                    "inline_blackdetect_passed": True,
                    "inline_blackdetect_max_duration_seconds": 0.749999,
                }
            ),
        )
        with self.assertRaises(SystemExit):
            verify.inline_blackdetect_max(
                {
                    "inline_blackdetect_passed": True,
                    "inline_blackdetect_max_duration_seconds": 0.75,
                }
            )

    def test_test_render_range_and_verifier_tolerance_are_unchanged(self):
        compose_source = COMPOSE_PATH.read_text(encoding="utf-8")
        verify_source = Path(verify.__file__).read_text(encoding="utf-8")
        self.assertIn("1.0 <= test_max <= 15.0", compose_source)
        self.assertIn("max_seconds + 0.55", verify_source)


if __name__ == "__main__":
    unittest.main()
