"""Fail-closed Wacky boundaries while a second channel shares runtime/main.

These tests intentionally exercise the *existing* Wacky entrypoints. Zodiac
must use a separate workflow, environment, state and runtime entrypoint.
"""
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from base.contract import EXPECTED_YOUTUBE_CHANNEL_ID, marker_tag, validate_content_id
from output.state import GitHubState, RecoveryBlocked
from output.transfer import authenticated_channel, prepare_upload, upload_new

ROOT = Path(__file__).resolve().parents[2]
WACKY_ID = "UCvrq2m9G4yrwPfL_X-QPzMA"


def youtube_with_channels(channels):
    youtube = MagicMock()
    youtube.channels.return_value.list.return_value.execute.return_value = {"items": channels}
    return youtube


class ChannelIsolationTests(unittest.TestCase):
    def test_wacky_channel_identity_is_fixed_and_valid(self):
        self.assertEqual(WACKY_ID, EXPECTED_YOUTUBE_CHANNEL_ID)
        youtube = youtube_with_channels([{"id": WACKY_ID}])
        self.assertEqual(WACKY_ID, authenticated_channel(youtube)["id"])
        youtube.channels.return_value.list.assert_called_once_with(
            part="id,snippet,contentDetails", mine=True
        )

    def test_wacky_rejects_wrong_missing_or_ambiguous_channel(self):
        for channels in (
            [],
            [{"id": "UCother"}],
            [{"id": WACKY_ID}, {"id": "UCother"}],
        ):
            with self.subTest(channels=channels):
                with self.assertRaisesRegex(RecoveryBlocked, "pinned production channel"):
                    authenticated_channel(youtube_with_channels(channels))

    def test_zodiac_environment_override_cannot_redirect_wacky(self):
        youtube = youtube_with_channels([{"id": "UCzodiac-not-wacky"}])
        with patch.dict(os.environ, {
            "EXPECTED_YOUTUBE_CHANNEL_ID": "UCzodiac-not-wacky",
            "YOUTUBE_CHANNEL_ID": "UCzodiac-not-wacky",
            "ZODIAC_YOUTUBE_CHANNEL_ID": "UCzodiac-not-wacky",
        }):
            with self.assertRaises(RecoveryBlocked):
                authenticated_channel(youtube)

    def test_wrong_channel_blocks_before_state_access_or_upload(self):
        for upload_stage in (prepare_upload, upload_new):
            with self.subTest(stage=upload_stage.__name__):
                youtube = youtube_with_channels([{"id": "UCwrong"}])
                state = MagicMock()
                args = (state, "request.json", {}, {}, youtube)
                if upload_stage is upload_new:
                    args = (state, "request.json", {}, {}, Path("unused.mp4"), youtube)
                with self.assertRaisesRegex(RecoveryBlocked, "pinned production channel"):
                    upload_stage(*args)
                state.load.assert_not_called()
                state.create.assert_not_called()
                youtube.videos.return_value.insert.assert_not_called()

    def test_wacky_identifiers_cannot_be_used_for_zodiac_state(self):
        cid = "wd-" + "a" * 24
        self.assertTrue(marker_tag(cid).startswith("wd-id-"))
        self.assertEqual(cid, validate_content_id(cid))
        with self.assertRaises(ValueError):
            validate_content_id("zd-" + "a" * 24)
        with self.assertRaises(ValueError):
            GitHubState.allowed("content/results/zd-" + "a" * 24 + ".json")

    def test_existing_wacky_workflow_is_exclusive(self):
        workflow = (ROOT / ".github/workflows/single.yml").read_text(encoding="utf-8")
        self.assertIn("environment: exec", workflow)
        self.assertIn("python runtime/transport.py fetch", workflow)
        self.assertIn("python runtime/core.py --manifest /tmp/runtime-execution.json", workflow)
        self.assertIn("secrets.PRIVATE_STATE_REPOSITORY", workflow)
        self.assertIn("secrets.PRIVATE_STATE_TOKEN", workflow)
        for name in ("RUNTIME_AUTH_A", "RUNTIME_AUTH_B", "RUNTIME_AUTH_C"):
            self.assertIn("secrets." + name, workflow)
        self.assertNotIn("runtime/zodiac/", workflow)
        self.assertNotIn("zodiac.yml", workflow)

    def test_wacky_entrypoints_do_not_call_zodiac_code(self):
        for rel in (
            "runtime/core.py",
            "runtime/transport.py",
            "runtime/engine/pipeline.py",
            "runtime/output/transfer.py",
            "runtime/output/state.py",
            "runtime/output/verify.py",
            "runtime/output/result.py",
            "runtime/guard/schema.py",
            "runtime/base/contract.py",
            "runtime/analytics_remote.py",
        ):
            with self.subTest(path=rel):
                source = (ROOT / rel).read_text(encoding="utf-8")
                self.assertNotIn("runtime/zodiac/", source)
                self.assertNotIn("from zodiac", source)
                self.assertNotIn("import zodiac", source)
                self.assertNotIn("zodiac-exec", source)

    def test_zodiac_workflow_if_added_is_isolated(self):
        path = ROOT / ".github/workflows/zodiac.yml"
        if not path.is_file():
            return  # No Zodiac code exists yet. Enforce rules from first introduction.
        source = path.read_text(encoding="utf-8")
        self.assertIn("environment: zodiac-exec", source)
        self.assertNotIn("environment: exec\n", source)
        self.assertIn("runtime/zodiac/", source)
        for forbidden in (
            "python runtime/core.py",
            "python runtime/transport.py",
            "skyfremen/youtube-workflow",
            "skyfremen/youtube-analytics-data",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
