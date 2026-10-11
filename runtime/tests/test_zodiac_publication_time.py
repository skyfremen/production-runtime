"""Public performance follows YouTube publication, not the reserved upload slot."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import analytics
from zodiac.analytics import PROFILE
from test_zodiac_analytics import CID, VIDEO, fixture

NOW = datetime(2026, 10, 11, 2, 30, tzinfo=timezone.utc)
ACTUAL = '2026-10-11T01:30:00Z'
RESERVED = '2026-10-12T01:30:00Z'


class ZodiacPublicationTimeTests(unittest.TestCase):
    def snapshot(self, root, privacy='public', actual=ACTUAL, reserved=RESERVED):
        result = fixture(root)
        result['publish_at'] = reserved
        path = root / f'content/results/{CID}.json'
        path.write_text(json.dumps(result))
        original = path.read_bytes()
        remote = {'items': [{'id': VIDEO, 'snippet': {'publishedAt': actual},
            'status': {'privacyStatus': privacy}, 'statistics': {'viewCount': '17'},
            'contentDetails': {'duration': 'PT6S'}}]}
        with patch('analytics.youtube_data', return_value=remote), \
                patch('analytics.collect_analytics_api', return_value=({}, True, [])), \
                patch('analytics.collect_optional_analytics_reports', return_value=({}, [])), \
                patch('analytics.collect_per_video_traffic_sources', return_value=({}, [])):
            snap = analytics.snapshot(root, 'token', NOW, profile=PROFILE)
        self.assertEqual(original, path.read_bytes(), 'Production receipt must remain immutable')
        return snap

    def test_early_publication_is_collected_before_reserved_slot(self):
        with tempfile.TemporaryDirectory() as td:
            snap = self.snapshot(Path(td))
        self.assertEqual([CID], [v['content_id'] for v in snap['videos']])
        video = snap['videos'][0]
        self.assertEqual(ACTUAL, video['publish_at'])
        self.assertEqual(RESERVED, video['scheduled_publish_at'])
        self.assertEqual('youtube_publication', video['publication_time_source'])
        self.assertEqual(1, video['age_hours'])
        self.assertEqual(17, video['metrics']['views'])

    def test_late_publication_uses_actual_age(self):
        with tempfile.TemporaryDirectory() as td:
            snap = self.snapshot(Path(td), reserved='2026-10-09T01:30:00Z')
        self.assertEqual(1, snap['videos'][0]['age_hours'])
        self.assertEqual(ACTUAL, snap['videos'][0]['publish_at'])

    def test_private_and_unlisted_videos_are_not_public_performance(self):
        for privacy in ('private', 'unlisted'):
            with self.subTest(privacy=privacy), tempfile.TemporaryDirectory() as td:
                snap = self.snapshot(Path(td), privacy=privacy, reserved='2026-10-09T01:30:00Z')
                self.assertEqual([], snap['videos'])

    def test_unknown_invalid_future_and_old_public_times_are_excluded(self):
        for actual in (None, 'invalid', '2026-10-11T01:30:00', RESERVED, '2026-08-01T00:00:00Z'):
            with self.subTest(actual=actual), tempfile.TemporaryDirectory() as td:
                snap = self.snapshot(Path(td), actual=actual, reserved='2026-10-09T01:30:00Z')
                self.assertEqual([], snap['videos'])

    def test_actual_time_controls_window_even_when_reservation_is_old(self):
        with tempfile.TemporaryDirectory() as td:
            snap = self.snapshot(Path(td), reserved='2026-08-01T00:00:00Z')
        self.assertEqual([CID], [v['content_id'] for v in snap['videos']])

    def test_historical_observations_rebase_without_rewriting_raw_snapshots(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            current = self.snapshot(root)
            self.assertEqual(1, len(current['videos']))
            path = root / 'realtime/2026-10-11/analytics-20261011T020000Z.json'
            path.parent.mkdir(parents=True)
            old_video = {**current['videos'][0], 'publish_at': '2026-10-09T01:30:00Z', 'age_hours': 48.5}
            old_video.pop('publication_time_source')
            path.write_text(json.dumps({'collected_at': '2026-10-11T02:00:00Z', 'videos': [old_video]}))
            original = path.read_bytes()
            before = path.with_name('analytics-20261011T010000Z.json')
            before.write_text(json.dumps({'collected_at': '2026-10-11T01:00:00Z', 'videos': [old_video]}))
            snapshots = analytics.all_snapshots(root, current, cid_re=PROFILE.content_id_re)
            observed = next(s for s in snapshots if s['collected_at'] == '2026-10-11T02:00:00Z')['videos'][0]
            self.assertEqual(.5, observed['age_hours'])
            self.assertEqual(ACTUAL, observed['publish_at'])
            self.assertEqual([], next(s for s in snapshots if s['collected_at'] == '2026-10-11T01:00:00Z')['videos'])
            self.assertEqual(original, path.read_bytes())
            rows = analytics.performance_rows(snapshots, cid_re=PROFILE.content_id_re)
            self.assertEqual('09:30', rows[0]['publish_slot_sgt'])

    def test_drama_keeps_reserved_time_behavior(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = fixture(root)
            cid = 'wd-' + 'a' * 24
            (root / f'content/results/{cid}.json').write_text(json.dumps({**result, 'content_id': cid, 'publish_at': RESERVED}))
            self.assertEqual([], analytics.eligible_videos(root, {}, NOW))


if __name__ == '__main__':
    unittest.main()
