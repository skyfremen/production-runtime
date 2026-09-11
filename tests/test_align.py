import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transform.align import AlignmentError, group_aligned_words, normalize_token, validate_alignment
from transform.process import build_caption_events


class CaptionAlignmentTests(unittest.TestCase):
    def test_normalization_handles_punctuation_contractions_and_numbers(self):
        self.assertEqual(normalize_token("weeks,"), "WEEKS")
        self.assertEqual(normalize_token("don't"), "DON'T")
        self.assertEqual(normalize_token("24"), "TWENTY|FOUR")
        self.assertEqual(normalize_token("2-hour"), "TWO|HOUR")

    def test_exact_aligned_word_timing_drives_ass_event(self):
        def fake_aligner(**_kwargs):
            return [
                {"word": "FOR", "start": 0.10, "end": 0.22},
                {"word": "WEEKS,", "start": 0.23, "end": 0.48},
                {"word": "EVERYONE", "start": 0.50, "end": 0.82},
            ], {"caption_alignment_backend": "fake-ctc", "caption_alignment_word_count": 3, "caption_alignment_coverage": 1.0}

        events, metadata = build_caption_events(
            "FOR WEEKS, EVERYONE", [("FOR WEEKS, EVERYONE", 24000)], 1.0,
            start_offset=2.20, narration_path=Path("/tmp/not-used.wav"), aligner=fake_aligner,
        )
        self.assertEqual(len(events), 1)
        self.assertIn("0:00:02.30", events[0])
        self.assertIn("0:00:03.02", events[0])
        self.assertIn("FOR WEEKS, EVERYONE", events[0].replace(r"\N", " "))
        self.assertEqual(metadata["caption_timing_mode"], "word_aligned")
        self.assertEqual(metadata["caption_alignment_backend"], "fake-ctc")
        self.assertEqual(metadata["caption_alignment_word_count"], 3)
        self.assertGreaterEqual(metadata["caption_alignment_duration_seconds"], 0)

    def test_grouping_prefers_natural_boundaries_over_rigid_three_words(self):
        words = [
            {"word": "FOR", "start": 0.00, "end": 0.10},
            {"word": "WEEKS,", "start": 0.11, "end": 0.30},
            {"word": "EVERYONE", "start": 0.31, "end": 0.55},
            {"word": "STAYED", "start": 0.56, "end": 0.74},
            {"word": "TWO", "start": 0.75, "end": 0.85},
            {"word": "UNPAID", "start": 0.86, "end": 1.08},
            {"word": "HOURS", "start": 1.09, "end": 1.28},
        ]
        rendered = [" ".join(item["word"] for item in group) for group in group_aligned_words(words)]
        self.assertEqual(rendered[0], "FOR WEEKS,")
        self.assertEqual(rendered[1], "EVERYONE STAYED")
        self.assertTrue(all(1 <= len(group.split()) <= 5 for group in rendered))

    def test_validation_rejects_bad_and_incomplete_alignment(self):
        bad_cases = [
            ([{"word": "FOR", "start": 0.2, "end": 0.1}], "FOR", 1.0),
            ([{"word": "FOR", "start": -0.2, "end": 0.1}], "FOR", 1.0),
            ([{"word": "FOR", "start": 0.1, "end": 1.2}], "FOR", 1.0),
        ]
        for words, text, duration in bad_cases:
            with self.assertRaises(AlignmentError):
                validate_alignment(words, text, duration)
        with self.assertRaises(AlignmentError):
            validate_alignment([{"word": "FOR", "start": 0.1, "end": 0.2}], "FOR WEEKS EVERYONE", 1.0, min_coverage=0.90)

    def test_alignment_failure_uses_estimated_fallback_and_reports_metadata(self):
        def failed_aligner(**_kwargs):
            raise AlignmentError("synthetic failure")

        events, metadata = build_caption_events(
            "Then payroll saw it", [("Then payroll saw it", 24000)], 1.0,
            start_offset=2.80, narration_path=Path("/tmp/not-used.wav"), aligner=failed_aligner,
        )
        self.assertTrue(events)
        self.assertIn("0:00:02.80", events[0])
        self.assertEqual(metadata["caption_timing_mode"], "estimated_fallback")
        self.assertEqual(metadata["caption_alignment_word_count"], 0)
        self.assertGreaterEqual(metadata["caption_alignment_duration_seconds"], 0)
        self.assertIn("synthetic failure", metadata["caption_alignment_error"])

    def test_aligned_story_subtitles_start_only_after_card_transition(self):
        def fake_aligner(**_kwargs):
            return [
                {"word": "THEN", "start": 0.00, "end": 0.20},
                {"word": "PAYROLL", "start": 0.21, "end": 0.50},
            ], {"caption_alignment_backend": "fake-ctc", "caption_alignment_word_count": 2, "caption_alignment_coverage": 1.0}

        events, _ = build_caption_events(
            "THEN PAYROLL", [("THEN PAYROLL", 12000)], 0.5,
            start_offset=3.10, narration_path=Path("/tmp/not-used.wav"), aligner=fake_aligner,
        )
        self.assertTrue(events)
        self.assertIn("0:00:03.10", events[0])
        self.assertNotIn("0:00:00.00", "\n".join(events))


if __name__ == "__main__":
    unittest.main()

