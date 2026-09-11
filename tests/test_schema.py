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
        "schema_version": 3,
        "content_id": "wd-20990910T000000-synthetic-proof-z9y8x7",
        "channel": {"name": "Wacky" + " Dramas", "handle": "@WACKY" + "DRAMAS"},
        "story": {
            "category": "WORKPLACE",
            "story_type": "BACKFIRE",
            "hook": "The Backup He Forgot About",
            "script": (
                "My boss told the team the file had never existed. "
                "I opened the archived workspace and found the timestamped copy."
            ),
            "card_emojis": ["💼", "🗂️", "😳", "💾", "🔥"],
        },
        "narration": {"engine": "kokoro", "voice": "af_heart", "speed": 1.75},
        "visual": {
            "background_primary_id": "satisfying-001",
            "background_backup_id": "satisfying-002",
        },
        "youtube": {
            "title": selected_title,
            "description": (
                "My boss said the file never existed—until the archived timestamp "
                "proved otherwise. Would you have confronted him?"
            ),
            "hashtags": [
                "#Shorts",
                "#Wacky" + "Dramas",
                "#WorkplaceDrama",
                "#Storytime",
            ],
            "tags": [
                "wacky" + " dramas",
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

    def test_noncanonical_schema_is_rejected(self):
        data = valid_request()
        data["schema_version"] = 99
        self.assertIn("schema_version must be 3", validate_request_data(data))

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
        data["channel"]["handle"] = "@WACKY" + "INSIGHTS"
        self.assertTrue(validate_request_data(data))

    def test_primary_backup_must_differ(self):
        data = valid_request()
        data["visual"]["background_backup_id"] = data["visual"]["background_primary_id"]
        self.assertTrue(
            any("must differ" in error for error in validate_request_data(data))
        )

    def test_fixture_is_isolated_per_call(self):
        first = valid_request()
        second = valid_request()
        first["planning"]["attributes"]["conflict"] = "CHANGED"
        self.assertNotEqual(first, second)
        self.assertEqual(second, copy.deepcopy(second))


if __name__ == "__main__":
    unittest.main()
