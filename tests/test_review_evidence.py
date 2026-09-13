import unittest
from pathlib import Path

from review_evidence import _pexels_https_url, representative_timestamps


ROOT = Path(__file__).resolve().parents[1]


class ReviewEvidenceTests(unittest.TestCase):
    def test_representative_timestamps_are_evenly_distributed(self):
        self.assertEqual(
            representative_timestamps(60.0, 5),
            [10.0, 20.0, 30.0, 40.0, 50.0],
        )

    def test_non_pexels_visual_source_is_rejected(self):
        with self.assertRaises(ValueError):
            _pexels_https_url('https://example.com/video.mp4', 'preview_video_url')

    def test_review_workflow_is_stateless_read_only_runtime_evidence(self):
        workflow = (ROOT / '.github/workflows/review-evidence.yml').read_text(encoding='utf-8')
        self.assertIn('workflow_dispatch:', workflow)
        self.assertIn('request_id:', workflow)
        self.assertIn('source_sha:', workflow)
        self.assertIn('discovery_result:', workflow)
        self.assertIn('PRIVATE_STATE_TOKEN', workflow)
        self.assertIn('PRIVATE_STATE_REPOSITORY', workflow)
        self.assertIn('runtime/review_evidence.py', workflow)
        self.assertIn('background-review-evidence-${{ inputs.request_id }}', workflow)
        self.assertIn('contents: read', workflow)
        self.assertNotIn('contents: write', workflow)
        self.assertNotIn('packages: write', workflow)
        self.assertNotIn('videos().insert', workflow)
        self.assertNotIn('state_sink.py', workflow)

    def test_private_approval_is_never_owned_by_public_runtime(self):
        source = (ROOT / 'runtime/review_evidence.py').read_text(encoding='utf-8')
        self.assertIn('sole authority for visual/editorial approval', source)
        self.assertIn('must never set verified_preview', source)
        self.assertNotIn('"verified_preview": True', source)


if __name__ == '__main__':
    unittest.main()
