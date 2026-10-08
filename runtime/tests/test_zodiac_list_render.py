"""Tests list-first silent Zodiac cards; requires optional Pillow for media checks."""
import copy
import tempfile
import unittest
from pathlib import Path
try:
    from zodiac import list_renderer
    from zodiac.renderer import check_render_prerequisites, compose
    from zodiac.artifacts import create_artifact
    HAS_PIL = True
except ModuleNotFoundError as e:
    if e.name != "PIL":
        raise
    HAS_PIL = False
from zodiac.entrypoint import HandoffRejected, validate_envelope
from test_zodiac_lane import envelope, resign

SIGNS = "aries taurus gemini cancer leo virgo libra scorpio sagittarius capricorn aquarius pisces".split()
RESULTS = ["Forgets at lunch", "Never forgets", "Laughs it off", "Needs a hug",
           "Writes a novel", "Makes a spreadsheet", "Talks it out", "Keeps receipts",
           "Goes for a walk", "Adds a reminder", "Turns it into a joke", "Forgives tomorrow"]

def list_envelope():
    e = envelope(1)
    c = e["requests"][0]["creative"]
    c["format_family"] = "zodiac_traits"
    c["display_mode"] = "full_screen_list"
    c["list_style"] = "sign_results"
    c["task_prompt"] = "How long each zodiac sign stays mad"
    c["duration_seconds"] = 12
    c["target_identity_and_coverage"] = {
        "kind":"sign","universal":True,"subset_label":None,
        "identities":SIGNS,"results":dict(zip(SIGNS,RESULTS))}
    c["timed_scenes"] = [{"start":0,"end":12,"kind":"full_list",
        "visible_text":[c["task_prompt"]] + [f"{s}: {v}" for s,v in zip(SIGNS,RESULTS)],
        "stable_seconds":12,"reading_load_words":56}]
    c["opening_variant_decision"]["options"][0]["promise"] = "How long"
    c["first_view_payoff"] = "Every sign has its own playful grudge duration."
    return resign(e)

class ListContractTests(unittest.TestCase):
    def test_all_results_visible_at_time_zero_in_contract(self):
        a=validate_envelope(list_envelope())
        self.assertEqual(a["validated_requests"][0]["editorial"]["display_mode"],"full_screen_list")

    def test_missing_result_at_frame_zero_denied(self):
        e=list_envelope()
        e["requests"][0]["creative"]["timed_scenes"][0]["visible_text"].pop()
        resign(e)
        with self.assertRaisesRegex(HandoffRejected,"first-frame list entry missing"):
            validate_envelope(e)

    def test_list_must_not_flash(self):
        e=list_envelope()
        e["requests"][0]["creative"]["timed_scenes"][0]["stable_seconds"]=3
        resign(e)
        with self.assertRaisesRegex(HandoffRejected,"list must remain stable"):
            validate_envelope(e)

@unittest.skipUnless(HAS_PIL, "media dependencies belong in private Zodiac CI")
class ListMediaTests(unittest.TestCase):
    def test_one_screen_with_motion_and_safe_fit(self):
        c=list_envelope()["requests"][0]["creative"]
        info=list_renderer.layout(c)
        self.assertEqual(info["identity_count"],12)
        self.assertEqual(len(info["entries"]),12)
        self.assertEqual(compose(c,0,0).size,(1080,1920))
        self.assertNotEqual(compose(c,0,0).tobytes(), compose(c,3,0).tobytes())
        check_render_prerequisites(c)

    def test_reject_unreadable_very_long_result(self):
        c=list_envelope()["requests"][0]["creative"]
        c["target_identity_and_coverage"]["results"]["aries"]="X"*250
        with self.assertRaisesRegex(HandoffRejected,"answer does not fit|outside phone-safe width"):
            check_render_prerequisites(c)

    def test_reject_more_than_18_rows(self):
        c=list_envelope()["requests"][0]["creative"]
        c["target_identity_and_coverage"]["identities"]=[f"z{i}" for i in range(19)]
        c["target_identity_and_coverage"]["results"]={f"z{i}":"Short" for i in range(19)}
        with self.assertRaisesRegex(HandoffRejected,"2–18"):
            check_render_prerequisites(c)

    def test_real_list_mp4_qc(self):
        validated=validate_envelope(list_envelope())
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/"actual-video-list"
            result=create_artifact(validated,out)
            self.assertEqual(result["videos"],1)
            self.assertTrue((out/"qc-report.json").is_file())
            self.assertEqual(len(list((out/"previews").glob("*.png"))),3)
            self.assertEqual(len(list((out/"videos").glob("*.mp4"))),1)

if __name__ == "__main__":
    unittest.main()
