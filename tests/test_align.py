import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transform.align import AlignmentError, group_aligned_words, normalize_token, validate_alignment
from transform.process import (
    CAPTION_ACTIVE_ASS,
    CAPTION_EMPHASIS_ASS,
    CAPTION_PUNCHLINE_ASS,
    CAPTION_PUNCHLINE_ACTIVE_OUTLINE,
    _caption_ass_focus_text,
    build_caption_events,
    highlighted_caption_events,
)
from transform.semantic import resolve_semantic_span


def strip_ass_overrides(text):
    parts = text.split(',', 9)
    payload = parts[9] if len(parts) == 10 else text
    payload = re.sub(r"\{[^{}]*\}", "", payload)
    return payload.replace(r"\N", " ")


class CaptionAlignmentTests(unittest.TestCase):
    def test_normalization_handles_punctuation_contractions_and_numbers(self):
        self.assertEqual(normalize_token("weeks,"), "WEEKS")
        self.assertEqual(normalize_token("don't"), "DON'T")
        self.assertEqual(normalize_token("24"), "TWENTY|FOUR")
        self.assertEqual(normalize_token("2-hour"), "TWO|HOUR")

    def test_exact_aligned_word_timing_drives_highlight_events(self):
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
        self.assertEqual(len(events), 3)
        self.assertIn("0:00:02.30", events[0])
        self.assertIn("0:00:03.02", events[-1])
        self.assertTrue(all(strip_ass_overrides(event) == "FOR WEEKS, EVERYONE" for event in events))
        self.assertTrue(all(f"{{\\c{CAPTION_ACTIVE_ASS}}}" in event for event in events))
        self.assertEqual(metadata["caption_timing_mode"], "word_aligned")
        self.assertEqual(metadata["caption_alignment_backend"], "fake-ctc")
        self.assertEqual(metadata["caption_alignment_word_count"], 3)
        self.assertTrue(metadata["caption_word_highlight_applied"])
        self.assertEqual(metadata["caption_word_highlight_event_count"], 3)
        self.assertFalse(metadata["caption_semantic_emphasis_applied"])
        self.assertEqual(metadata["caption_punchline_match_status"], "missing_metadata")
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
        self.assertFalse(metadata["caption_word_highlight_applied"])
        self.assertFalse(metadata["caption_semantic_emphasis_applied"])
        self.assertEqual(metadata["caption_punchline_match_status"], "alignment_unavailable")
        self.assertEqual(metadata["caption_word_highlight_event_count"], 0)
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

    def test_contractions_punctuation_and_repeated_words_keep_stable_phrase(self):
        words = [
            {"word": "I", "start": 0.00, "end": 0.16},
            {"word": "DON'T", "start": 0.17, "end": 0.38},
            {"word": "KNOW.", "start": 0.39, "end": 0.63},
            {"word": "NO", "start": 0.70, "end": 0.86},
            {"word": "NO", "start": 0.87, "end": 1.03},
            {"word": "NO.", "start": 1.04, "end": 1.22},
        ]
        events, _rapid = highlighted_caption_events(words)
        self.assertGreaterEqual(len(events), 5)
        rendered = [strip_ass_overrides(event) for event in events]
        self.assertIn("I DON'T KNOW.", rendered)
        self.assertIn("NO NO NO.", rendered)
        self.assertTrue(all(f"{{\\c{CAPTION_ACTIVE_ASS}}}" in event for event in events))

    def test_rapid_words_group_at_most_three_words(self):
        words = [
            {"word": "NO", "start": 0.00, "end": 0.07},
            {"word": "NO", "start": 0.08, "end": 0.15},
            {"word": "NO", "start": 0.16, "end": 0.23},
            {"word": "WAIT", "start": 0.30, "end": 0.55},
        ]
        events, rapid_groups = highlighted_caption_events(words)
        self.assertEqual(rapid_groups, 1)
        first = events[0]
        self.assertEqual(first.count(f"{{\\c{CAPTION_ACTIVE_ASS}}}"), 3)
        self.assertIn("NO", strip_ass_overrides(first))

    def test_semantic_match_is_scoped_to_punchline_then_emphasis(self):
        words = [
            {"word": "HE", "start": 0.00, "end": 0.12},
            {"word": "HAD", "start": 0.13, "end": 0.25},
            {"word": "DELETED", "start": 0.26, "end": 0.52},
            {"word": "THE", "start": 0.53, "end": 0.63},
            {"word": "WRONG", "start": 0.64, "end": 0.88},
            {"word": "FOLDER.", "start": 0.89, "end": 1.16},
        ]
        punchline = {
            "text": "He had deleted the wrong folder.",
            "emphasis_text": "wrong folder",
            "type": "REVERSAL",
        }
        resolved = resolve_semantic_span(words, punchline)
        self.assertEqual(resolved["status"], "matched")
        self.assertEqual(resolved["punchline_word_count"], 6)
        self.assertEqual(resolved["emphasis_word_count"], 2)
        self.assertAlmostEqual(resolved["punchline_start"], 0.0)
        self.assertAlmostEqual(resolved["emphasis_start"], 0.64)
        self.assertAlmostEqual(resolved["emphasis_end"], 1.16)

    def test_active_punchline_uses_orange_without_yellow_or_reflow(self):
        group = [
            {"word": "HE", "start": 0.0, "end": 0.1},
            {"word": "DELETED", "start": 0.1, "end": 0.3},
            {"word": "THE", "start": 0.3, "end": 0.4},
            {"word": "WRONG", "start": 0.4, "end": 0.6},
            {"word": "FOLDER.", "start": 0.6, "end": 0.8},
        ]
        ordinary = _caption_ass_focus_text(group, active_indices=(3,))
        punchline = _caption_ass_focus_text(
            group,
            active_indices=(3,),
            punchline_indices=range(5),
            emphasis_indices=(3, 4),
        )
        self.assertEqual(strip_ass_overrides(ordinary), strip_ass_overrides(punchline))
        self.assertIn(CAPTION_ACTIVE_ASS, ordinary)
        self.assertNotIn(CAPTION_PUNCHLINE_ASS, ordinary)
        self.assertIn(CAPTION_PUNCHLINE_ASS, punchline)
        self.assertNotIn(CAPTION_ACTIVE_ASS, punchline)
        self.assertEqual(punchline.count(CAPTION_PUNCHLINE_ASS), 1)
        self.assertIn(f"\\bord{CAPTION_PUNCHLINE_ACTIVE_OUTLINE}", punchline)

    def test_punchline_event_replaces_yellow_with_orange_only_while_active(self):
        words = [
            {"word": "THEN", "start": 0.00, "end": 0.20},
            {"word": "HE", "start": 0.21, "end": 0.40},
            {"word": "HAD", "start": 0.41, "end": 0.60},
            {"word": "DELETED", "start": 0.61, "end": 0.88},
            {"word": "THE", "start": 0.89, "end": 1.04},
            {"word": "WRONG", "start": 1.05, "end": 1.30},
            {"word": "FOLDER.", "start": 1.31, "end": 1.58},
        ]
        resolved = resolve_semantic_span(words, {
            "text": "He had deleted the wrong folder.",
            "emphasis_text": "wrong folder",
            "type": "REVERSAL",
        })
        events, _rapid = highlighted_caption_events(words, semantic_resolution=resolved)
        orange_events = [event for event in events if CAPTION_PUNCHLINE_ASS in event]
        yellow_events = [event for event in events if CAPTION_ACTIVE_ASS in event]
        self.assertTrue(orange_events)
        self.assertTrue(yellow_events)
        self.assertTrue(all(CAPTION_ACTIVE_ASS not in event for event in orange_events))
        self.assertTrue(all(
            f"\\bord{CAPTION_PUNCHLINE_ACTIVE_OUTLINE}" in event
            for event in orange_events
        ))

    def test_semantic_match_failure_keeps_real_alignment_and_word_highlight(self):
        def fake_aligner(**_kwargs):
            return [
                {"word": "HE", "start": 0.00, "end": 0.20},
                {"word": "LEFT.", "start": 0.21, "end": 0.50},
            ], {"caption_alignment_backend": "fake-ctc", "caption_alignment_word_count": 2, "caption_alignment_coverage": 1.0}

        events, metadata = build_caption_events(
            "HE LEFT.", [("HE LEFT.", 12000)], 0.5,
            narration_path=Path("/tmp/not-used.wav"), aligner=fake_aligner,
            punchline={"text": "He stayed.", "emphasis_text": "stayed", "type": "REVERSAL"},
        )
        self.assertEqual(metadata["caption_timing_mode"], "word_aligned")
        self.assertTrue(metadata["caption_word_highlight_applied"])
        self.assertFalse(metadata["caption_semantic_emphasis_applied"])
        self.assertEqual(metadata["caption_punchline_match_status"], "punchline_not_found")
        self.assertTrue(any(CAPTION_ACTIVE_ASS in event for event in events))
        self.assertTrue(all(CAPTION_EMPHASIS_ASS not in event for event in events))

    def test_semantic_flag_is_independent_from_word_highlight_flag(self):
        def fake_aligner(**_kwargs):
            return [
                {"word": "HE", "start": 0.00, "end": 0.20},
                {"word": "LEFT.", "start": 0.21, "end": 0.50},
            ], {"caption_alignment_backend": "fake-ctc", "caption_alignment_word_count": 2, "caption_alignment_coverage": 1.0}

        with patch.dict(os.environ, {"CAPTION_SEMANTIC_EMPHASIS_ENABLED": "false"}, clear=False):
            events, metadata = build_caption_events(
                "HE LEFT.", [("HE LEFT.", 12000)], 0.5,
                narration_path=Path("/tmp/not-used.wav"), aligner=fake_aligner,
                punchline={"text": "He left.", "emphasis_text": "left", "type": "REVEAL"},
            )
        self.assertTrue(metadata["caption_word_highlight_applied"])
        self.assertFalse(metadata["caption_semantic_emphasis_enabled"])
        self.assertFalse(metadata["caption_semantic_emphasis_applied"])
        self.assertEqual(metadata["caption_punchline_match_status"], "disabled")
        self.assertTrue(any(CAPTION_ACTIVE_ASS in event for event in events))
        self.assertTrue(all(CAPTION_EMPHASIS_ASS not in event for event in events))

    def test_highlighting_can_be_disabled_without_losing_word_aligned_timing(self):
        def fake_aligner(**_kwargs):
            return [
                {"word": "THEN", "start": 0.00, "end": 0.20},
                {"word": "PAYROLL", "start": 0.21, "end": 0.50},
            ], {"caption_alignment_backend": "fake-ctc", "caption_alignment_word_count": 2, "caption_alignment_coverage": 1.0}

        with patch.dict(os.environ, {"CAPTION_WORD_HIGHLIGHT_ENABLED": "false"}, clear=False):
            events, metadata = build_caption_events(
                "THEN PAYROLL", [("THEN PAYROLL", 12000)], 0.5,
                narration_path=Path("/tmp/not-used.wav"), aligner=fake_aligner,
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(metadata["caption_timing_mode"], "word_aligned")
        self.assertFalse(metadata["caption_word_highlight_enabled"])
        self.assertFalse(metadata["caption_word_highlight_applied"])
        self.assertNotIn(f"{{\\c{CAPTION_ACTIVE_ASS}}}", events[0])


if __name__ == "__main__":
    unittest.main()
