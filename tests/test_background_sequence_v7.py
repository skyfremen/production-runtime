import unittest

from guard.schema import (
    CONCATENATED_FIT_TO_SHORT_MODE,
    validate_background_sequence,
)
from resources.resolve import apply_concatenated_fit_to_short_treatment
from transform.process import _sequence_caption_readability_score


class BackgroundSequenceV7Tests(unittest.TestCase):
    def sequence(self):
        return [
            {"background_id": "satisfying-px-1", "segment_start_seconds": 0, "segment_duration_seconds": 80},
            {"background_id": "satisfying-px-2", "segment_start_seconds": 1, "segment_duration_seconds": 80},
            {"background_id": "satisfying-px-3", "segment_start_seconds": 2, "segment_duration_seconds": 80},
        ]

    def registry(self):
        return {
            "assets": [
                {"id": "satisfying-px-1", "caption_readability_score": 92},
                {"id": "satisfying-px-2", "caption_readability_score": 88},
                {"id": "satisfying-px-3", "caption_readability_score": 94},
            ]
        }

    def test_valid_sequence(self):
        self.assertEqual(validate_background_sequence(self.sequence()), [])

    def test_duplicate_rejected(self):
        sequence = self.sequence()
        sequence[1]["background_id"] = sequence[0]["background_id"]
        self.assertTrue(validate_background_sequence(sequence))

    def test_mode_is_stable(self):
        self.assertEqual(CONCATENATED_FIT_TO_SHORT_MODE, "concatenated_fit_to_short")
        self.assertTrue(callable(apply_concatenated_fit_to_short_treatment))

    def test_sequence_readability_uses_weakest_selected_asset(self):
        selection = {"background_sequence": self.sequence()}
        self.assertEqual(_sequence_caption_readability_score(selection, self.registry()), 88.0)

    def test_sequence_readability_fails_closed_when_score_missing(self):
        registry = self.registry()
        del registry["assets"][1]["caption_readability_score"]
        selection = {"background_sequence": self.sequence()}
        with self.assertRaisesRegex(RuntimeError, "missing caption readability"):
            _sequence_caption_readability_score(selection, registry)


if __name__ == "__main__":
    unittest.main()
