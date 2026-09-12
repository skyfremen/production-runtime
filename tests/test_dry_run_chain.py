import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRY_RUN = ROOT / '.github/workflows/dry-run.yml'


class DryRunChainTests(unittest.TestCase):
    def text(self):
        return DRY_RUN.read_text(encoding='utf-8')

    def test_dispatch_accepts_opaque_correlation_and_expected_sha(self):
        text = self.text()
        trigger = text.split('concurrency:', 1)[0]
        self.assertIn('correlation_id:', trigger)
        self.assertIn('expected_sha:', trigger)
        self.assertIn("dr_[0-9a-f]{24}", text)
        self.assertIn("actual != expected", text)
        self.assertIn('E_DRY_SHA', text)

    def test_dispatch_concurrency_cannot_cancel_linked_validation(self):
        text = self.text()
        self.assertIn(
            "group: dry-run-${{ github.event_name }}-${{ inputs.correlation_id || github.ref || github.run_id }}",
            text,
        )
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name != 'workflow_dispatch' }}",
            text,
        )
        self.assertIn("format('dry-run-{0}', inputs.correlation_id)", text)

    def test_base_changes_build_and_exercise_candidate_image(self):
        text = self.text()
        self.assertIn("startswith('base/')", text)
        self.assertIn('--file base/Dockerfile', text)
        self.assertIn('runtime-dry-run-candidate:${GITHUB_SHA}', text)
        self.assertGreaterEqual(text.count('${{ steps.image.outputs.image }}'), 3)
        self.assertIn('timeout-minutes: 60', text)


if __name__ == '__main__':
    unittest.main()
