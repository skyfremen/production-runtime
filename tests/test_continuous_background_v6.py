import unittest

from resources.resolve import _fit_range
from transform.process import _strip_background_stream_loop


class ContinuousBackgroundRuntimeTests(unittest.TestCase):
    def test_fit_math(self):
        self.assertAlmostEqual(_fit_range({"segment_start_seconds": 0, "segment_duration_seconds": 300}, 150)[2], 2.0)
        self.assertAlmostEqual(_fit_range({"segment_start_seconds": 0, "segment_duration_seconds": 240}, 160)[2], 1.5)

    def test_insufficient_source_fails(self):
        with self.assertRaises(RuntimeError): _fit_range({"segment_start_seconds": 0, "segment_duration_seconds": 100}, 160)

    def test_normal_render_removes_infinite_loop(self):
        command = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", "background.asset", "-t", "150", "out.mp4"]
        stripped = _strip_background_stream_loop(command)
        self.assertNotIn("-stream_loop", stripped)
        self.assertEqual(stripped[2:4], ["-i", "background.asset"])

    def test_dry_run_uses_short_continuous_subrange(self):
        start, duration, rate = _fit_range({"segment_start_seconds": 12, "segment_duration_seconds": 300}, 8, test_mode=True)
        self.assertEqual(start, 12); self.assertEqual(duration, 16); self.assertEqual(rate, 2.0)


if __name__ == "__main__": unittest.main()
