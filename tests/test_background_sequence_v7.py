import unittest

from guard.schema import (
    CONCATENATED_FIT_TO_SHORT_MODE,
    validate_background_sequence,
)
from resources.resolve import apply_concatenated_fit_to_short_treatment


class BackgroundSequenceV7Tests(unittest.TestCase):
    def sequence(self):
        return [
            {"background_id": "satisfying-px-1", "segment_start_seconds": 0, "segment_duration_seconds": 80},
            {"background_id": "satisfying-px-2", "segment_start_seconds": 1, "segment_duration_seconds": 80},
            {"background_id": "satisfying-px-3", "segment_start_seconds": 2, "segment_duration_seconds": 80},
        ]

    def test_valid_sequence(self):
        self.assertEqual(validate_background_sequence(self.sequence()), [])

    def test_duplicate_rejected(self):
        sequence = self.sequence()
        sequence[1]["background_id"] = sequence[0]["background_id"]
        self.assertTrue(validate_background_sequence(sequence))

    def test_mode_is_stable(self):
        self.assertEqual(CONCATENATED_FIT_TO_SHORT_MODE, "concatenated_fit_to_short")
        self.assertTrue(callable(apply_concatenated_fit_to_short_treatment))


if __name__ == "__main__":
    unittest.main()
