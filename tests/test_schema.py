import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from guard.schema import validate_request_data
from profile.config import EDITORIAL_WEIGHTS, TITLE_WEIGHTS


def _title_candidate(title, style, score):
    return {
        "title": title,
        "style": style,
        "truthful": True,
        "score": score,
        "score_components": {key: score for key in TITLE_WEIGHTS},
    }


def valid_request():
    title = "The Backup Exposed What Really Happened #Shorts"
    return {
        "schema_version": 4,
        "content_id": "wd-20990910T000000-test-a00000",
        "channel": {"name": "Wacky Dramas", "handle": "@WACKYDRAMAS"},
        "story": {
            "category": "WORKPLACE",
            "story_type": "BACKFIRE",
            "hook": "The Backup He Forgot About",
            "script": (
                "I don't panic when files vanish. No, no, no—I check the backup first. "
                "My boss said there was no proof. I opened the archive. "
                "He had deleted the wrong folder."
            ),
            "card_emojis": ["💼", "🗂️", "😳", "💾", "🔥"],
            "lead_gender": "female",
            "story_tone": "dramatic",
            "punchline": {
                "text": "He had deleted the wrong folder.",
                "emphasis_text": "wrong folder",
                "type": "REVERSAL",
            },
        },
        "narration": {"engine": "kokoro", "voice": "af_bella", "speed": 1.75},
        "visual": {
            "background_primary_id": "satisfying-001",
            "background_backup_id": "satisfying-002",
        },
        "youtube": {
            "title": title,
            "description": "The archived timestamp changed the whole argument.",
            "hashtags": ["#Shorts", "#WackyDramas", "#WorkplaceDrama", "#Storytime"],
            "tags": ["wacky dramas", "workplace drama", "boss story", "storytime"],
            "category_id": "24",
            "made_for_kids": False,
        },
        "publication": {
            "mode": "scheduled",
            "timezone": "Asia/Singapore",
            "publish_at": "2099-09-09T16:00:00Z",
        },
        "planning": {
            "plan_date": "2099-09-10",
            "editorial_score": 88.0,
            "editorial_components": {key: 88 for key in EDITORIAL_WEIGHTS},
            "analytics_score": None,
            "analytics_weight": 0.0,
            "final_score": 87.5,
            "title_candidates": [
                _title_candidate(title, "HIDDEN_REVELATION", 91),
                _title_candidate("I Checked the Archive and Found the Proof #Shorts", "DISCOVERY", 86),
                _title_candidate("It Looked Normal Until the Timestamp Appeared #Shorts", "NORMAL_TO_ABNORMAL", 84),
                _title_candidate("I Kept the Archive and the Story Changed #Shorts", "DECISION_CONSEQUENCE", 83),
                _title_candidate("Hours Before the Audit, I Found the Backup #Shorts", "COUNTDOWN", 82),
            ],
            "selected_title_score": 91.0,
            "hook_score": 90.0,
            "selection_class": "exploit",
            "selection_reason": "Strong contradiction, proof-driven escalation and clear reversal.",
            "similarity": {"max_recent_similarity": 0.21},
            "attributes": {
                "subtype": "EVIDENCE_BACKFIRE",
                "conflict": "HIDDEN_FILE",
                "primary_emotion": "INJUSTICE",
                "protagonist_role": "EMPLOYEE",
                "antagonist_role": "BOSS",
                "opening_style": "CONTRADICTION",
                "title_style": "HIDDEN_REVELATION",
                "ending_style": "REVERSAL",
            },
            "target_duration_seconds": 151,
        },
    }


class RequestSchemaTests(unittest.TestCase):
    def test_valid_request_passes(self):
        self.assertEqual(validate_request_data(valid_request()), [])

    def test_immediate_publication_passes_with_null_publish_at(self):
        data = valid_request()
        data["publication"] = {
            "mode": "immediate",
            "timezone": "Asia/Singapore",
            "publish_at": None,
        }
        self.assertEqual(validate_request_data(data), [])

    def test_immediate_publication_rejects_non_null_publish_at(self):
        data = valid_request()
        data["publication"]["mode"] = "immediate"
        self.assertTrue(any("must be null" in error for error in validate_request_data(data)))

    def test_only_supported_schema_versions_are_accepted(self):
        data = valid_request()
        data["schema_version"] = 3
        self.assertIn("schema_version must be 4, 5 or 6", validate_request_data(data))

    def test_publication_and_planning_are_required(self):
        for field in ("publication", "planning"):
            with self.subTest(field=field):
                data = valid_request()
                data.pop(field)
                errors = validate_request_data(data)
                self.assertTrue(any(field in error for error in errors))

    def test_old_field_fails(self):
        data = valid_request()
        data["setup"] = "obsolete"
        self.assertTrue(any("setup" in error for error in validate_request_data(data)))

    def test_wrong_brand_fails(self):
        data = valid_request()
        data["channel"]["name"] = "Other"
        self.assertTrue(validate_request_data(data))

    def test_primary_backup_must_differ(self):
        data = valid_request()
        data["visual"]["background_backup_id"] = data["visual"]["background_primary_id"]
        self.assertTrue(validate_request_data(data))

    def test_invalid_voice_gender_pair_fails(self):
        data = valid_request()
        data["narration"]["voice"] = "am_echo"
        self.assertTrue(validate_request_data(data))

    def test_punchline_is_required_and_must_match_script(self):
        data = valid_request()
        data["story"].pop("punchline")
        self.assertTrue(validate_request_data(data))
        data = valid_request()
        data["story"]["punchline"]["text"] = "This sentence is not in the story."
        self.assertTrue(validate_request_data(data))

    def test_emphasis_is_short_and_inside_resolved_punchline(self):
        data = valid_request()
        data["story"]["punchline"]["emphasis_text"] = "not present"
        self.assertTrue(validate_request_data(data))
        data = valid_request()
        data["story"]["punchline"]["emphasis_text"] = "one two three four five six"
        data["story"]["punchline"]["text"] = "one two three four five six"
        data["story"]["script"] += " one two three four five six"
        self.assertTrue(validate_request_data(data))

    def test_ambiguous_punchline_fails_closed(self):
        data = valid_request()
        data["story"]["script"] += " He had deleted the wrong folder."
        self.assertTrue(validate_request_data(data))

    def test_semantic_normalization_accepts_case_and_punctuation(self):
        data = valid_request()
        data["story"]["punchline"]["text"] = "HE HAD DELETED THE WRONG FOLDER"
        data["story"]["punchline"]["emphasis_text"] = "WRONG FOLDER"
        self.assertEqual(validate_request_data(data), [])

    def test_canonical_voice_mapping(self):
        cases = {
            ("female", "natural"): "af_heart",
            ("female", "dramatic"): "af_bella",
            ("male", "natural"): "am_echo",
            ("male", "dramatic"): "am_fenrir",
        }
        for (gender, tone), voice in cases.items():
            with self.subTest(gender=gender, tone=tone):
                data = valid_request()
                data["story"]["lead_gender"] = gender
                data["story"]["story_tone"] = tone
                data["narration"]["voice"] = voice
                self.assertEqual(validate_request_data(data), [])


if __name__ == "__main__":
    unittest.main()
