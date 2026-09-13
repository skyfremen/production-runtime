import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from resources import resolve


class BackgroundSequenceMuxerTests(unittest.TestCase):
    def test_assembly_forces_mp4_container_for_asset_filename(self):
        sequence = [
            {"background_id": "satisfying-px-1", "segment_start_seconds": 0, "segment_duration_seconds": 80},
            {"background_id": "satisfying-px-2", "segment_start_seconds": 0, "segment_duration_seconds": 80},
            {"background_id": "satisfying-px-3", "segment_start_seconds": 0, "segment_duration_seconds": 80},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resolved = []
            for index in range(3):
                source = root / f"segment-{index}.asset"
                source.write_bytes(b"segment")
                resolved.append({"path": source})
            target = root / "background.asset"
            captured = {}

            def fake_run(command, **kwargs):
                captured["command"] = command
                target.write_bytes(b"x" * 12000)
                return SimpleNamespace(returncode=0, stderr="")

            with (
                patch.object(resolve.subprocess, "run", side_effect=fake_run),
                patch.object(resolve, "_media_duration_seconds", return_value=240.0),
                patch.object(resolve, "sha256_file", return_value="a" * 64),
            ):
                metrics = resolve._assemble_sequence(resolved, sequence, target)

            self.assertEqual(target.suffix, ".asset")
            self.assertEqual(captured["command"][-3:], ["-f", "mp4", str(target)])
            self.assertEqual(metrics["background_sequence_output_duration_seconds"], 240.0)


if __name__ == "__main__":
    unittest.main()
