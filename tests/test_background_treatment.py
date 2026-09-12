import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
TESTS = ROOT / "tests"
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(TESTS))

from guard import schema
from output import receipt
from resources import resolve as media_resolver
from test_schema import valid_request


def v5_request():
    data = copy.deepcopy(valid_request())
    data["schema_version"] = 5
    data["visual"].update({
        "background_primary_treatment": {
            "segment_start_seconds": 2.0,
            "segment_duration_seconds": 10.0,
            "playback_rate": 1.4,
        },
        "background_backup_treatment": {
            "segment_start_seconds": 0.0,
            "segment_duration_seconds": None,
            "playback_rate": 1.2,
        },
    })
    return data


class TreatmentSchemaTests(unittest.TestCase):
    def test_v4_remains_valid_during_migration(self):
        self.assertEqual(schema.validate_request_data(valid_request()), [])

    def test_v5_requires_both_slot_treatments(self):
        data = v5_request()
        self.assertEqual(schema.validate_request_data(data), [])
        data["visual"].pop("background_backup_treatment")
        errors = schema.validate_request_data(data)
        self.assertTrue(any("background_backup_treatment" in error for error in errors))

    def test_v5_rejects_out_of_bounds_rate_and_invalid_full_source_start(self):
        data = v5_request()
        data["visual"]["background_primary_treatment"]["playback_rate"] = 2.01
        data["visual"]["background_backup_treatment"]["segment_start_seconds"] = 1.0
        errors = schema.validate_request_data(data)
        self.assertTrue(any("playback_rate" in error and "<= 2" in error for error in errors))
        self.assertTrue(any("must be 0" in error for error in errors))

    def test_treatment_for_slot_returns_identity_for_v4(self):
        self.assertEqual(
            schema.treatment_for_slot(valid_request(), "primary"),
            {
                "segment_start_seconds": 0.0,
                "segment_duration_seconds": None,
                "playback_rate": 1.0,
            },
        )


class TreatmentExecutionTests(unittest.TestCase):
    def test_treatment_operates_on_normalized_frames_without_rescaling(self):
        treatment = {
            "segment_start_seconds": 2.0,
            "segment_duration_seconds": 10.0,
            "playback_rate": 1.4,
        }
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "background.asset"
            target.write_bytes(b"n" * 12000)
            normalized_probe = {
                "codec": "h264", "width": 1080, "height": 1920, "fps": 30.0
            }

            def fake_run(command, **_kwargs):
                Path(command[-1]).write_bytes(b"t" * 13000)
                return mock.MagicMock(returncode=0, stderr="")

            with mock.patch.object(
                media_resolver, "_media_duration_seconds", side_effect=[20.0, 7.143]
            ), mock.patch.object(
                media_resolver.subprocess, "run", side_effect=fake_run
            ) as runner, mock.patch.object(
                media_resolver, "probe_video", return_value=normalized_probe
            ), mock.patch.object(
                media_resolver, "analyze_caption_region", return_value={"protection_alpha": 0.2}
            ):
                metrics = media_resolver.apply_background_treatment(target, treatment, 90)

            self.assertTrue(metrics["background_treatment_applied"])
            self.assertEqual(metrics["background_treatment_input_duration_seconds"], 20.0)
            self.assertAlmostEqual(metrics["background_treatment_output_duration_seconds"], 7.143)
            command = runner.call_args.args[0]
            filters = command[command.index("-vf") + 1]
            self.assertIn("trim=start=2.000000:duration=10.000000", filters)
            self.assertIn("setpts=(PTS-STARTPTS)/1.40000000", filters)
            self.assertIn("fps=30", filters)
            self.assertNotIn("scale=", filters)
            self.assertNotIn("crop=", filters)
            self.assertEqual(target.read_bytes(), b"t" * 13000)

    def test_segment_beyond_resolved_media_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "background.asset"
            target.write_bytes(b"n" * 12000)
            with mock.patch.object(media_resolver, "_media_duration_seconds", return_value=8.0):
                with self.assertRaises(RuntimeError):
                    media_resolver.apply_background_treatment(
                        target,
                        {
                            "segment_start_seconds": 4.0,
                            "segment_duration_seconds": 5.0,
                            "playback_rate": 1.2,
                        },
                    )


class TreatmentReceiptTests(unittest.TestCase):
    def test_v5_receipt_records_exact_executed_treatment(self):
        request = v5_request()
        selection = {
            "background_selection": "primary",
            "background_treatment": copy.deepcopy(
                request["visual"]["background_primary_treatment"]
            ),
        }
        legacy_candidate = {"background_usage": {"logical_asset_id": "satisfying-001"}}
        with mock.patch.object(
            receipt, "_legacy_build_receipt", return_value=copy.deepcopy(legacy_candidate)
        ):
            result = receipt.build_receipt("request.json", request, {}, selection, {})
        self.assertEqual(result["schema_version"], 5)
        self.assertEqual(
            result["background_treatment"],
            request["visual"]["background_primary_treatment"],
        )
        self.assertEqual(result["background_usage"]["playback_rate"], 1.4)
        self.assertEqual(result["background_usage"]["segment_start_seconds"], 2.0)

    def test_v5_receipt_rejects_treatment_drift(self):
        request = v5_request()
        selection = {
            "background_selection": "primary",
            "background_treatment": {
                **request["visual"]["background_primary_treatment"],
                "playback_rate": 1.6,
            },
        }
        with mock.patch.object(
            receipt, "_legacy_build_receipt", return_value={"background_usage": {}}
        ):
            with self.assertRaises(receipt.RecoveryBlocked):
                receipt.build_receipt("request.json", request, {}, selection, {})


if __name__ == "__main__":
    unittest.main()
