import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from profile.config import EDITORIAL_WEIGHTS, TITLE_WEIGHTS
from guard.schema import validate_request_data


def _title_candidate(title, style, score):
    return {
        "title": title,
        "style": style,
        "truthful": True,
        "score": score,
        "score_components": {key: score for key in TITLE_WEIGHTS},
    }


def valid_request():
    selected_title = "My Boss Said the File Was Gone… Then IT Found the Backup #Shorts"
    return {
        "schema_version": 4,
        "content_id": "wd-20990910T000000-synthetic-proof-z9y8x7",
        "channel": {"name": "Wa" + "cky" + " Dramas", "handle": "@WA" + "CKY" + "DRAMAS"},
        "story": {
            "category": "WORKPLACE",
            "story_type": "BACKFIRE",
            "hook": "The Backup He Forgot About",
            "script": (
                "My boss told the team the file had never existed. "
                "I opened the archived workspace and found the timestamped copy. "
                "He had deleted the wrong folder."
            ),
            "card_emojis": ["💼", "🗂️", "😳", "💾", "🔥"],
            "lead_gender": "female",
            "story_tone": "natural",
            "punchline": {
                "text": "He had deleted the wrong folder.",
                "emphasis_text": "wrong folder",
                "type": "REVERSAL",
            },
        },
        "narration": {"engine": "kokoro", "voice": "af_heart", "speed": 1.75},
        "visual": {
            "background_primary_id": "satisfying-001",
            "background_backup_id": "satisfying-002",
        },
        "youtube": {
            "title": selected_title,
            "description": (
                "The archived timestamp changed the whole argument. "
                "Would you have confronted the person who denied it?"
            ),
            "hashtags": [
                "#Shorts",
                "#Wa" + "cky" + "Dramas",
                "#WorkplaceDrama",
                "#Storytime",
            ],
            "tags": [
                "wa" + "cky" + " dramas",
                "workplace drama",
                "boss story",
                "office conflict",
                "evidence backfire",
                "storytime",
            ],
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
                _title_candidate(selected_title, "HIDDEN_REVELATION", 91),
                _title_candidate(
                    "I Checked the Archive and Found the Proof #Shorts", "DISCOVERY", 86
                ),
                _title_candidate(
                    "It Looked Like a Normal File Error… Until I Saw the Timestamp #Shorts",
                    "NORMAL_TO_ABNORMAL",
                    84,
                ),
                _title_candidate(
                    "I Refused to Delete the Archive. Then My Boss Changed His Story #Shorts",
                    "DECISION_CONSEQUENCE",
                    83,
                ),
                _title_candidate(
                    "Hours Before the Audit, I Found the Missing Backup #Shorts",
                    "COUNTDOWN",
                    82,
                ),
            ],
            "selected_title_score": 91.0,
            "hook_score": 90.0,
            "selection_class": "exploit",
            "selection_reason": (
                "Strong contradiction, proof-driven escalation and clear reversal."
            ),
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

    def test_only_schema_v4_is_supported(self):
        data = valid_request()
        data["schema_version"] = 3
        self.assertIn("schema_version must be 4", validate_request_data(data))

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
        self.assertTrue(
            any("unexpected" in error or "forbidden" in error for error in validate_request_data(data))
        )

    def test_wrong_brand_fails(self):
        data = valid_request()
        data["channel"]["handle"] = "@WA" + "CKY" + "INSIGHTS"
        self.assertTrue(validate_request_data(data))

    def test_primary_backup_must_differ(self):
        data = valid_request()
        data["visual"]["background_backup_id"] = data["visual"]["background_primary_id"]
        self.assertTrue(any("must differ" in error for error in validate_request_data(data)))

    def test_canonical_voice_mapping(self):
        cases = (
            ("female", "natural", "af_heart"),
            ("female", "dramatic", "af_bella"),
            ("male", "general", "am_echo"),
            ("male", "comedy", "am_fenrir"),
        )
        for gender, tone, voice in cases:
            with self.subTest(gender=gender, tone=tone):
                data = valid_request()
                data["story"].update(lead_gender=gender, story_tone=tone)
                data["narration"]["voice"] = voice
                self.assertEqual(validate_request_data(data), [])

    def test_invalid_voice_gender_pair_fails(self):
        data = valid_request()
        data["narration"]["voice"] = "am_fenrir"
        self.assertTrue(any("narration.voice" in error for error in validate_request_data(data)))

    def test_punchline_is_required_and_must_match_script(self):
        data = valid_request()
        data["story"].pop("punchline")
        self.assertTrue(any("punchline" in error for error in validate_request_data(data)))

        data = valid_request()
        data["story"]["punchline"]["text"] = "He deleted a totally different drive."
        self.assertTrue(any("must occur" in error for error in validate_request_data(data)))

    def test_emphasis_is_short_and_inside_resolved_punchline(self):
        data = valid_request()
        data["story"]["punchline"]["emphasis_text"] = "the team the file had never existed"
        errors = validate_request_data(data)
        self.assertTrue(any("at most 5 words" in error for error in errors))
        self.assertTrue(any("inside" in error for error in errors))

    def test_semantic_normalization_accepts_case_and_punctuation(self):
        data = valid_request()
        data["story"]["punchline"]["text"] = "HE HAD DELETED THE WRONG FOLDER!"
        data["story"]["punchline"]["emphasis_text"] = "WRONG FOLDER!"
        self.assertEqual(validate_request_data(data), [])

    def test_ambiguous_punchline_fails_closed(self):
        data = valid_request()
        data["story"]["script"] += " He had deleted the wrong folder."
        self.assertTrue(any("unambiguous" in error for error in validate_request_data(data)))

    def test_fixture_is_isolated_per_call(self):
        first = valid_request()
        second = valid_request()
        first["planning"]["attributes"]["conflict"] = "CHANGED"
        self.assertNotEqual(first, second)
        self.assertEqual(second, copy.deepcopy(second))


if __name__ == "__main__":
    unittest.main()
