import json
from pathlib import Path
import tempfile
import unittest
import analytics
from zodiac import analytics as lane
from test_zodiac_analytics import fixture, observation, CID


class CategoryAnalyticsTests(unittest.TestCase):
    def test_backfill_snapshot_labels_are_explicit_and_original_requests_stay_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); fixture(root)
            request=root/'content/requests/rq-fixture.json'; before=request.read_bytes()
            (root/'data').mkdir()
            (root/'data/content-classifications.json').write_text(json.dumps(
                {'version':1,'classifications':{CID:{'category':'romance','topic_tags':['rejection']}}}))
            creative=lane.creative_map(root)[CID]
            self.assertEqual('romance',creative['category'])
            self.assertEqual('editorial_backfill',creative['classification_source'])
            self.assertEqual(before,request.read_bytes())
            video=observation(); video['creative']=creative
            summary=analytics.build_summary(root,{'collected_at':'2026-10-10T00:00:00Z',
                'videos':[video]},profile=lane.PROFILE)
            self.assertEqual(1,summary['category_tracking']['backfilled_videos'])
            self.assertEqual(0,summary['category_tracking']['planner_recorded_videos'])

    def test_immutable_request_labels_reach_creative_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); fixture(root)
            path = root / 'content/requests/rq-fixture.json'
            request = json.loads(path.read_text())
            request['items'][0]['zodiac'].update(category='friendship', topic_tags=['trust'])
            path.write_text(json.dumps(request))
            creative = lane.creative_map(root)[CID]
            self.assertEqual('friendship', creative['category'])
            self.assertEqual(['trust'], creative['topic_tags'])
            self.assertEqual('sign_results', creative['format'])

    def test_legacy_format_is_not_a_content_category(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); fixture(root)
            self.assertEqual('unknown', lane.creative_map(root)[CID]['category'])
            current = {'collected_at': '2026-10-10T00:00:00Z', 'videos': [observation()]}
            summary = analytics.build_summary(root, current, profile=lane.PROFILE)
            self.assertEqual({'unknown'}, set(summary['category_performance']))
            self.assertEqual(1, summary['category_tracking']['unclassified_videos'])

    def test_category_and_tag_patterns_require_mature_samples(self):
        videos = []
        for category, ratio, age in [('friendship', 80, 72), ('romance', 20, 72), ('family', 99, 24)]:
            for i in range(5):
                video = observation(cid=f'za-{category}-fixture-{i:03}', ratio=ratio, age=age)
                video['creative'].update(category=category, topic_tags=['trust', category])
                videos.append(video)
        with tempfile.TemporaryDirectory() as td:
            summary = analytics.build_summary(Path(td), {'collected_at': '2026-10-10T00:00:00Z',
                'videos': videos}, profile=lane.PROFILE)
        self.assertEqual(5, summary['category_performance']['friendship']['sample_size'])
        self.assertEqual(15, summary['topic_tag_performance']['trust']['sample_size'])
        self.assertEqual(15, summary['category_tracking']['classified_videos'])
        projection = lane.planner_projection(summary)
        supported = projection['creative_signals']['supported_patterns']
        weak = projection['creative_signals']['weak_patterns']
        self.assertIn(('category', 'friendship'), [(p['dimension'], p['value']) for p in supported])
        self.assertIn(('category', 'romance'), [(p['dimension'], p['value']) for p in weak])
        self.assertNotIn(('category', 'family'), [(p['dimension'], p['value']) for p in supported])
        self.assertTrue(any(p['dimension'] == 'topic_tag' for p in supported))
        self.assertEqual('friendship', next(e for e in summary['top_examples']
            if 'friendship' in e['content_id'])['category'])
        self.assertLessEqual(len((json.dumps(projection, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode()),16000)

    def test_unknown_category_never_becomes_a_supported_pattern(self):
        videos = [observation(cid=f'za-legacy-fixture-{i:03}', ratio=90) for i in range(5)]
        with tempfile.TemporaryDirectory() as td:
            summary = analytics.build_summary(Path(td), {'collected_at':'2026-10-10T00:00:00Z',
                'videos':videos}, profile=lane.PROFILE)
        patterns = lane.planner_projection(summary)['creative_signals']['supported_patterns']
        self.assertFalse(any(p['dimension'] == 'category' for p in patterns))

