import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rendering.render import (
    CARD_TRANSITION_SECONDS, START_LEAD_SECONDS, X264_CRF, X264_PRESET, caption_events,
)


class IntroSequenceTests(unittest.TestCase):

    def test_subtitles_start_only_after_card_transition(self):
        story_start = 2.80 + CARD_TRANSITION_SECONDS
        events = caption_events(
            "Then payroll saw it",
            [("Then payroll saw it", 24000)],
            1.0,
            start_offset=story_start,
        )
        self.assertTrue(events)
        self.assertIn("0:00:03.10", events[0])
        self.assertNotIn("0:00:00.00", "\n".join(events))

    def test_fallback_subtitles_also_start_after_card_transition(self):
        story_start = 2.50 + CARD_TRANSITION_SECONDS
        events = caption_events(
            "Then payroll saw it",
            [],
            1.0,
            start_offset=story_start,
        )
        self.assertTrue(events)
        self.assertIn("0:00:02.80", events[0])
        self.assertNotIn("0:00:00.00", "\n".join(events))

    def test_renderer_fades_card_after_lead_and_intro_and_offsets_captions(self):
        source = (ROOT / "rendering/render.py").read_text()
        self.assertEqual(START_LEAD_SECONDS, 0.50)
        self.assertIn("card_fade_start = START_LEAD_SECONDS + intro_duration", source)
        self.assertIn("card_fade_dur = CARD_TRANSITION_SECONDS", source)
        self.assertIn("start_offset=story_start", source)
        self.assertIn("lead_audio = np.zeros", source)
        self.assertIn("transition_audio = np.zeros", source)

    def test_encoder_uses_measured_quality_preserving_fast_profile(self):
        self.assertEqual(X264_PRESET, "superfast")
        self.assertEqual(X264_CRF, 19)


if __name__ == "__main__":
    unittest.main()
