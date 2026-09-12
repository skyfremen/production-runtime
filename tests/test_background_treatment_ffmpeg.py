import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from resources import resolve as media_resolver


class RealBackgroundTreatmentTests(unittest.TestCase):
    def test_real_ffmpeg_treatment_preserves_production_geometry(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "background.asset"
            generated = subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30",
                    "-t", "2.0", "-an", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-f", "mp4", str(target),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            original_bytes = target.stat().st_size
            with mock.patch.object(
                media_resolver,
                "analyze_caption_region",
                return_value={"protection_alpha": 0.2},
            ):
                metrics = media_resolver.apply_background_treatment(
                    target,
                    {
                        "segment_start_seconds": 0.25,
                        "segment_duration_seconds": 1.25,
                        "playback_rate": 1.25,
                    },
                    90,
                )

            probe = media_resolver.probe_video(target)
            self.assertTrue(metrics["background_treatment_applied"])
            self.assertEqual(probe["codec"], "h264")
            self.assertEqual((probe["width"], probe["height"]), (1080, 1920))
            self.assertAlmostEqual(probe["fps"], 30.0, places=2)
            self.assertGreater(target.stat().st_size, 10000)
            self.assertNotEqual(metrics["treated_background_bytes"], original_bytes)
            self.assertGreater(metrics["background_treatment_duration_seconds"], 0)
            self.assertGreater(metrics["background_treatment_output_duration_seconds"], 0.8)
            self.assertLess(metrics["background_treatment_output_duration_seconds"], 1.2)


if __name__ == "__main__":
    unittest.main()
