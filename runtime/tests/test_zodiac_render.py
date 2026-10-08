"""Real MP4 smoke tests for the independent Zodiac lane; fictional data only.

Run in separate Zodiac CI with Pillow, ffmpeg and DejaVu fonts installed.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest
import os
from unittest.mock import patch
from zodiac.artifacts import create_artifact
from zodiac.entrypoint import validate_envelope, HandoffRejected
from zodiac.renderer import compose, check_render_prerequisites
from test_zodiac_lane import envelope


class RenderTests(unittest.TestCase):
    def test_animated_frame_has_correct_size(self):
        c=envelope(1)["requests"][0]["creative"]
        im=compose(c, 0, 0)
        self.assertEqual(im.size, (1080, 1920))
        self.assertEqual(im.mode, "RGB")
        a=compose(c, 3, 0)
        b=compose(c, 3.5, 0)
        self.assertNotEqual(a.tobytes(), b.tobytes(), "decorative animation should move")

    def test_too_long_assignment_hard_rejected(self):
        c=envelope(1)["requests"][0]["creative"]
        c["target_identity_and_coverage"]["results"]["aries"] = "VERY LONG WORD"+"X"*200
        with self.assertRaisesRegex(HandoffRejected, "unbreakable|visible cell"):
            check_render_prerequisites(c)

    def test_unavailable_font_rejected(self):
        from zodiac import renderer
        c=envelope(1)["requests"][0]["creative"]
        with patch.object(renderer, "FONT_BOLD", "/missing-dejavu-bold.ttf"):
            with self.assertRaisesRegex(HandoffRejected, "font missing"):
                check_render_prerequisites(c)

    def test_real_mp4_and_integrity_and_no_upload(self):
        e=envelope(1)
        validated=validate_envelope(e)
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/"zodiac-private-preview"
            with patch.dict(os.environ, {k:"" for k in
                             ("PRIVATE_STATE_TOKEN","PRIVATE_STATE_REPOSITORY","RUNTIME_AUTH_A",
                              "RUNTIME_AUTH_B","RUNTIME_AUTH_C","PUBLIC_PRODUCTION_TOKEN")}):
                result=create_artifact(validated, out)
            self.assertEqual(result["videos"], 1)
            manifest=json.loads((out/"manifest.json").read_text())
            qc=json.loads((out/"qc-report.json").read_text())
            self.assertTrue(qc["passed"])
            self.assertFalse(manifest["youtube_upload_enabled"])
            video=manifest["videos"][0]
            self.assertEqual(video["width"],1080)
            self.assertEqual(video["height"],1920)
            self.assertEqual(video["fps"],30)
            self.assertEqual(video["audio_streams"],0)
            self.assertEqual(video["frame_count"],300)
            self.assertGreater(video["size_bytes"],10000)
            self.assertEqual(len(list((out/"previews").glob("*.png"))),4)
            import hashlib
            self.assertEqual(hashlib.sha256((out/video["file"]).read_bytes()).hexdigest(),
                             video["sha256"])

    def test_missing_duration_rejected_pre_encoding(self):
        e=envelope(1)
        e["requests"][0]["creative"]["timed_scenes"][-1]["end"]=9.5
        c=e["requests"][0]["creative"]
        with self.assertRaisesRegex(HandoffRejected, "incomplete|final|timeline"):
            check_render_prerequisites({**c, "timed_scenes":[
                *c["timed_scenes"][:-1],{**c["timed_scenes"][-1], "kind":"unknown"}]})

if __name__=="__main__":
    unittest.main()
