import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))

from publishing.finalize_receipt import build_receipt
from publishing.recovery_state import RecoveryBlocked, blob_sha
from test_recovery import MemoryState, fixture


class ResultReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.request, identity, record, _ = fixture()
        self.path = Path(self.temp.name) / (self.request["content_id"] + ".json")
        self.path.write_text(json.dumps(self.request))
        identity["request_blob_sha"] = blob_sha(self.path.read_bytes())
        record.update(identity)

        self.selection = {
            "background_asset_id": self.request["visual"]["background_primary_id"],
            "background_selection": "primary",
        }
        self.render = {
            "content_id": self.request["content_id"],
            "render_verified": True,
            "narration_engine": "kokoro",
            "narration_voice": "af_heart",
            "narration_speed": 1.75,
            "narration_seconds": 150.0,
            "video_seconds": 151.0,
            "resolution": "720x1280",
            "fps": 30,
            "video_codec": "h264",
            "audio_codec": "aac",
            "audio_stream_count": 1,
            "test_mode": False,
        }
        record.update({"background": self.selection, "render": self.render})
        publish_at = self.request["publication"]["publish_at"]
        self.upload = {
            **identity,
            "youtube_video_id": record["youtube_video_id"],
            "youtube_url": (
                "https://www.youtube.com/watch?v=" + record["youtube_video_id"]
            ),
            "upload_evidence": record,
            "uploaded_at": record["uploaded_at"],
            "recovered": True,
            "recovery_record_path": "evidence",
            "recovery_record_blob_sha": "c" * 40,
            "verification": {
                **identity,
                "passed": True,
                "state": "verified_scheduled",
                "privacy_status": "private",
                "publish_at": publish_at,
                "publish_at_absent": False,
                "youtube_video_id": record["youtube_video_id"],
                "channel_id": record["expected_channel_id"],
                "verified_at": "2099-09-09T15:55:00Z",
            },
        }
        workflow_patch = patch(
            "publishing.finalize_receipt.workflow_identity",
            return_value={
                "name": "Daily Production",
                "run_id": "20",
                "run_attempt": "2",
                "code_commit_sha": "d" * 40,
            },
        )
        workflow_patch.start()
        self.addCleanup(workflow_patch.stop)

    def build(self):
        return build_receipt(
            self.path, self.request, self.upload, self.selection, self.render
        )

    def test_complete_measured_provenance(self):
        receipt = self.build()
        self.assertEqual(receipt["schema_version"], 3)
        self.assertEqual(receipt["video_seconds"], 151.0)
        self.assertEqual(receipt["renderer_source_commit"], "b" * 40)
        self.assertEqual(receipt["verification_source_commit"], "d" * 40)
        self.assertEqual(receipt["upload_workflow"]["run_id"], "1")
        self.assertEqual(receipt["workflow_run_id"], "20")
        self.assertEqual(receipt["workflow_run_attempt"], "2")
        self.assertFalse(receipt["publish_at_absent"])
        self.assertEqual(receipt["publication_mode"], "scheduled")
        self.assertEqual(receipt["privacy_status"], "private")
        self.assertEqual(
            receipt["publish_at"], self.request["publication"]["publish_at"]
        )
        self.assertEqual(receipt["audio_stream_count"], 1)
        self.assertNotIn("test_kind", receipt)

    def test_performance_metrics_are_carried_into_receipt(self):
        self.render.update(
            {
                "x264_preset": "superfast",
                "x264_crf": 19,
                "tts_generation_duration_seconds": 2.3,
                "caption_alignment_duration_seconds": 3.4,
                "ffmpeg_duration_seconds": 4.5,
            }
        )
        metrics = self.build()["production_metrics"]
        self.assertEqual(metrics["x264_preset"], "superfast")
        self.assertEqual(metrics["x264_crf"], 19)
        self.assertEqual(metrics["tts_generation_duration_seconds"], 2.3)
        self.assertEqual(metrics["caption_alignment_duration_seconds"], 3.4)

    def test_unverified_video_cannot_create_receipt(self):
        self.upload["verification"]["passed"] = False
        with self.assertRaises(RecoveryBlocked):
            self.build()

    def test_changed_request_cannot_create_receipt(self):
        self.path.write_text(self.path.read_text() + " ")
        with self.assertRaises(RecoveryBlocked):
            self.build()

    def test_wrong_video_channel_or_schedule_cannot_create_receipt(self):
        original = copy.deepcopy(self.upload)
        cases = [
            ("youtube_video_id", "other000000"),
            ("channel_id", "foreign"),
            ("publish_at", "2099-09-09T17:00:00Z"),
        ]
        for key, value in cases:
            with self.subTest(key=key):
                self.upload = copy.deepcopy(original)
                self.upload["verification"][key] = value
                with self.assertRaises(RecoveryBlocked):
                    self.build()

    def test_unscheduled_request_cannot_create_receipt(self):
        self.request.pop("publication")
        self.path.write_text(json.dumps(self.request))
        self.upload["request_blob_sha"] = blob_sha(self.path.read_bytes())
        self.upload["upload_evidence"]["request_blob_sha"] = self.upload[
            "request_blob_sha"
        ]
        self.upload["verification"]["request_blob_sha"] = self.upload[
            "request_blob_sha"
        ]
        with self.assertRaisesRegex(RecoveryBlocked, "Scheduled"):
            self.build()

    def test_unverified_or_changed_render_cannot_create_receipt(self):
        self.render["render_verified"] = False
        with self.assertRaises(RecoveryBlocked):
            self.build()

    def test_failed_receipt_commit_then_rerun_preserves_one_immutable_receipt(self):
        from publishing import finalize_receipt
        from publishing.recovery_state import encoded_json, receipt_path, record_path
        from common.workflow_common import atomic_write_json

        identity = {
            key: self.upload[key]
            for key in (
                "content_id",
                "request_path",
                "request_blob_sha",
                "source_commit_sha",
            )
        }
        state = MemoryState()
        stored = state.create(
            record_path(identity["content_id"], "upload"),
            self.upload["upload_evidence"],
        )
        self.upload["recovery_record_blob_sha"] = stored.sha
        output = Path(self.temp.name) / "output"
        output.mkdir()
        for name, data in (
            ("upload_result.json", self.upload),
            ("background_selection.json", self.selection),
            ("render-metadata.json", self.render),
        ):
            atomic_write_json(output / name, data)

        real_create = state.create
        state.create = lambda path, data: (_ for _ in ()).throw(
            RecoveryBlocked("receipt commit failed")
        )
        with (
            patch("publishing.finalize_receipt.GitHubState", return_value=state),
            patch("publishing.finalize_receipt.identity_for", return_value=identity),
            patch("publishing.finalize_receipt.OUTPUT_DIR", output),
            patch("sys.argv", ["finalize_receipt", "--request", str(self.path)]),
        ):
            with self.assertRaises(RecoveryBlocked):
                finalize_receipt.main()
            self.assertIsNone(state.load(receipt_path(identity["content_id"])))
            state.create = real_create
            finalize_receipt.main()
            original = encoded_json(
                state.load(receipt_path(identity["content_id"])).data
            )
            self.upload["verification"]["verified_at"] = "2099-09-09T15:56:00Z"
            atomic_write_json(output / "upload_result.json", self.upload)
            finalize_receipt.main()
            self.assertEqual(
                original,
                encoded_json(state.load(receipt_path(identity["content_id"])).data),
            )
            self.assertEqual(len(state.writes), 2)


if __name__ == "__main__":
    unittest.main()

