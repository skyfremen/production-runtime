import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from resources.quality import MAX_PROTECTION_ALPHA, analyze_caption_region, protection_for_samples


class CaptionRegionQualityTests(unittest.TestCase):
    def make_clip(self, path, changing=False):
        if changing:
            source = (
                "color=black:s=180x320:r=6:d=1[dark];"
                "color=white:s=180x320:r=6:d=1[light];"
                "[dark][light]concat=n=2:v=1:a=0"
            )
            command = ["ffmpeg", "-y", "-hide_banner", "-v", "error",
                       "-filter_complex", source, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
        else:
            command = ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi",
                       "-i", "color=white:s=180x320:r=6:d=2", "-c:v", "libx264",
                       "-pix_fmt", "yuv420p", str(path)]
        subprocess.run(command, check=True)

    def test_dark_stable_source_uses_subtle_protection(self):
        self.assertEqual(protection_for_samples([70] * 12, [130] * 12, [2] * 12), 0.18)

    def test_bright_source_strengthens_protection(self):
        self.assertEqual(protection_for_samples([210] * 12, [250] * 12, [3] * 12), MAX_PROTECTION_ALPHA)

    def test_dark_to_bright_change_strengthens_protection(self):
        self.assertEqual(protection_for_samples([70, 190], [130, 225], [0, 40]), MAX_PROTECTION_ALPHA)

    def test_bright_footage_is_sampled_and_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bright.mp4"
            self.make_clip(path)
            result = analyze_caption_region(path, 2, 90)
        self.assertEqual(result["sample_count"], 12)
        self.assertEqual(result["protection_alpha"], MAX_PROTECTION_ALPHA)

    def test_changing_footage_is_sampled_across_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changing.mp4"
            self.make_clip(path, changing=True)
            result = analyze_caption_region(path, 2, 90)
        self.assertEqual(result["sample_count"], 12)
        self.assertGreater(result["max_temporal_luma_difference"], 32)
        self.assertEqual(result["protection_alpha"], MAX_PROTECTION_ALPHA)


if __name__ == "__main__":
    unittest.main()
