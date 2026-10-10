import importlib.util
import io
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import analytics
import analytics_remote

NOW=datetime(2026,10,10,0,tzinfo=timezone.utc)
CID='za-analytics-fixture-001'; VIDEO='abcdefghijk'; CHANNEL='UC'+'a'*22

def adapter(case):
    case.assertIsNotNone(importlib.util.find_spec('zodiac.analytics'),'Zodiac analytics adapter required')
    from zodiac import analytics as module
    return module

def fixture(root):
    item={'content_id':CID,'zodiac':{'title':'HOW EACH SIGN ACTS WHEN IGNORED','format':'sign_results','rows':[{'label':'Aries','answer':'Starts another conversation'}]},'youtube':{'title':'HOW EACH SIGN ACTS WHEN IGNORED'},'publication':{'mode':'scheduled'}}
    path=root/'content/requests/rq-fixture.json';path.parent.mkdir(parents=True);path.write_text(json.dumps({'publication':{'enabled':True,'channel_id':CHANNEL},'items':[item]}))
    result={'content_id':CID,'youtube_video_id':VIDEO,'publish_at':'2026-10-07T00:00:00Z','verified':True,'status':'scheduled'}
    path=root/f'content/results/{CID}.json';path.parent.mkdir(parents=True);path.write_text(json.dumps(result))
    return result

def observation(age=72,views=1000,ratio=40,cid=CID,format='sign_results'):
    return {'content_id':cid,'youtube_video_id':VIDEO,'age_hours':age,'publish_at':'2026-10-07T00:00:00Z','duration_seconds':6,
        'creative':{'title':'HOW EACH SIGN ACTS WHEN IGNORED','category':format,'format':format,'headline_length':'7_10_words','rows':[{'label':'Aries','answer':'Starts another conversation'}]},
        'metrics':{'views':views,'engaged_views':views*ratio/100,'shorts_source_views':views,'shorts_source_engaged_views':views*ratio/100,'shorts_source_engaged_view_rate_percentage':ratio,'average_view_duration':7,'average_view_percentage':116.7,'shares':10,'likes':50,'comments':5,'subscribers_gained':2}}

class ZodiacAnalyticsTests(unittest.TestCase):
    def test_transient_optional_report_failure_recovers_with_bounded_retries(self):
        lane=adapter(self); attempts=[]
        self.assertTrue(hasattr(lane.PROFILE,'optional_report_attempts'),'Zodiac transient retries must be configured')
        def report(params,token):
            if 'subscribedStatus' not in params.get('dimensions',''):return {}
            attempts.append(dict(params))
            if len(attempts)<3:
                raise HTTPError('https://example.invalid',500,'Internal Server Error',{},
                    io.BytesIO(b'{"error":{"errors":[{"reason":"internalError"}]}}'))
            return {'columnHeaders':[{'name':'subscribedStatus'},{'name':'views'}],
                'rows':[['UNSUBSCRIBED',400]]}
        with patch('time.sleep') as sleep:
            reports,warnings=analytics.collect_optional_analytics_reports('token',NOW,report,
                attempts=lane.PROFILE.optional_report_attempts,error_details=True)
        self.assertEqual([{'subscribedStatus':'UNSUBSCRIBED','views':400}],reports['subscriber_status']['rows'])
        self.assertEqual([],warnings)
        self.assertEqual(3,len(attempts));self.assertTrue(all(p==attempts[0] for p in attempts))
        self.assertEqual([1,2],[call.args[0] for call in sleep.call_args_list])

    def test_optional_report_retries_stop_and_permission_errors_are_not_retried(self):
        self.assertIn('attempts',inspect.signature(analytics.collect_optional_analytics_reports).parameters)
        for code,expected in ((500,3),(429,3),(403,1),(400,1)):
            attempted=[]
            def report(params,token):
                if 'subscribedStatus' not in params.get('dimensions',''):return {}
                attempted.append(params)
                raise HTTPError('https://example.invalid',code,'unavailable',{},io.BytesIO(b'{}'))
            with patch('time.sleep'):
                reports,warnings=analytics.collect_optional_analytics_reports('token',NOW,report,attempts=3,error_details=True)
            self.assertNotIn('subscriber_status',reports)
            self.assertEqual(expected,len(attempted))
            self.assertEqual([f'Optional YouTube Analytics report subscriber_status unavailable: HTTPError (HTTP {code})'],warnings)
        attempted=[]
        with patch('time.sleep') as sleep:
            _,warnings=analytics.collect_optional_analytics_reports('token',NOW,report)
        self.assertEqual(1,len(attempted));sleep.assert_not_called()
        self.assertEqual(['Optional YouTube Analytics report subscriber_status unavailable: HTTPError'],warnings)

    def test_optional_report_failure_identifies_http_cause_without_response_secrets(self):
        # Losing the opt-in formatter must make this assertion fail; Drama keeps its old warning.
        lane=adapter(self)
        payload={'error':{'code':400,'message':'The query is not supported.',
            'errors':[{'reason':'badRequest','message':'Bearer private-fixture-value'}]}}
        def report(params, token):
            if 'subscribedStatus' in params.get('dimensions',''):
                raise HTTPError('https://example.invalid/?token=private-fixture-value',400,
                    'private-fixture-value',{},io.BytesIO(json.dumps(payload).encode()))
            return {}
        reports,warnings=analytics.collect_optional_analytics_reports('token',NOW,report,
            error_details=lane.PROFILE.optional_error_details)
        self.assertIn('geography',reports)
        self.assertNotIn('subscriber_status',reports)
        self.assertEqual(['Optional YouTube Analytics report subscriber_status unavailable: HTTPError (HTTP 400; badRequest; unsupported query)'],warnings)
        self.assertNotIn('private-fixture-value',json.dumps(warnings))
        _,default_warnings=analytics.collect_optional_analytics_reports('token',NOW,report)
        self.assertEqual(['Optional YouTube Analytics report subscriber_status unavailable: HTTPError'],default_warnings)

    def test_optional_report_failure_handles_non_json_and_unknown_error_bodies(self):
        def report(params,token):
            if 'subscribedStatus' in params.get('dimensions',''):
                raise HTTPError('https://example.invalid',403,'private-fixture-value',{},
                    io.BytesIO(b'{"error":{"message":"private-fixture-value","errors":[{"reason":"private-fixture-value"}]}}'))
            return {}
        _,warnings=analytics.collect_optional_analytics_reports('token',NOW,report,error_details=True)
        self.assertEqual(['Optional YouTube Analytics report subscriber_status unavailable: HTTPError (HTTP 403)'],warnings)
        def malformed(params,token):
            raise HTTPError('https://example.invalid',503,'private-fixture-value',{},io.BytesIO(b'not JSON private-fixture-value'))
        _,warnings=analytics.collect_optional_analytics_reports('token',NOW,malformed,error_details=True)
        self.assertTrue(all(w.endswith('HTTPError (HTTP 503)') for w in warnings))
        def nested(params,token):
            if 'subscribedStatus' not in params.get('dimensions',''):return {}
            body=b'{"error":'+b'['*30000+b'0'+b']'*30000+b'}'
            raise HTTPError('https://example.invalid',500,'Internal Server Error',{},io.BytesIO(body))
        reports,warnings=analytics.collect_optional_analytics_reports('token',NOW,nested,error_details=True)
        self.assertIn('geography',reports)
        self.assertNotIn('subscriber_status',reports)
        self.assertEqual(['Optional YouTube Analytics report subscriber_status unavailable: HTTPError (HTTP 500)'],warnings)

    def test_snapshot_excludes_drama_future_unverified_and_abandoned(self):
        lane=adapter(self)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);result=fixture(root)
            for cid,changes in [('wd-'+'b'*24,{}),('za-future-fixture-001',{'publish_at':'2026-10-11T00:00:00Z'}),('za-unverified-fixture-001',{'verified':False}),('za-abandoned-fixture-001',{})]:
                (root/f'content/results/{cid}.json').write_text(json.dumps({**result,'content_id':cid,**changes}))
                request_path=root/'content/requests/rq-fixture.json';request=json.loads(request_path.read_text())
                request['items'].append({**request['items'][0],'content_id':cid});request_path.write_text(json.dumps(request))
            path=root/'content/abandonments/za-abandoned-fixture-001.json';path.parent.mkdir(parents=True);path.write_text('{}')
            with patch('analytics.collect_data_api',return_value={VIDEO:{'views':100,'duration_seconds':6}}),patch('analytics.collect_analytics_api',return_value=({},False,['no scope'])),patch('analytics.collect_optional_analytics_reports',return_value=({},[])),patch('analytics.collect_per_video_traffic_sources',return_value=({},[])):
                snap=analytics.snapshot(root,'token',NOW,profile=lane.PROFILE)
            self.assertEqual([CID],[v['content_id'] for v in snap['videos']])
            self.assertEqual('sign_results',snap['videos'][0]['creative']['format'])
            self.assertIsNone(snap['videos'][0]['metrics']['engaged_views']);self.assertIn('no scope',snap['warnings'])

    def test_channel_guard_rejects_wrong_handle_and_request_channel(self):
        lane=adapter(self)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root)
            for channel in ({'id':CHANNEL,'snippet':{'customUrl':'@WACKYDRAMAS'}},{'id':'UC'+'b'*22,'snippet':{'customUrl':'@WackyAstrology'}}):
                with patch('analytics.youtube_data',return_value={'items':[channel]}):
                    with self.assertRaisesRegex(RuntimeError,'channel'):lane.verify_channel(root,'token')
            with patch('analytics.youtube_data',return_value={'items':[{'id':CHANNEL,'snippet':{'customUrl':'@WackyAstrology'}}]}):self.assertEqual(CHANNEL,lane.verify_channel(root,'token'))

    def test_mature_patterns_prioritize_response_and_exclude_young_evidence(self):
        lane=adapter(self)
        videos=[observation(cid=f'za-one-fixture-{i:03}',ratio=80) for i in range(5)]+[observation(cid=f'za-two-fixture-{i:03}',format='ranking',ratio=20,views=100000) for i in range(5)]+[observation(age=24,cid=f'za-young-fixture-{i:03}',format='trait_matches',ratio=99) for i in range(8)]
        with tempfile.TemporaryDirectory() as td:summary=analytics.build_summary(Path(td),{'collected_at':'2026-10-10T00:00:00Z','videos':videos,'warnings':[],'analytics_reports':{}},profile=lane.PROFILE)
        projection=lane.planner_projection(summary);formats=[p for p in projection['creative_signals']['supported_patterns'] if p['dimension']=='format']
        self.assertEqual('sign_results',formats[0]['value']);self.assertNotIn('trait_matches',[p['value'] for p in formats]);self.assertEqual(80,formats[0]['shorts_source_engaged_view_rate_percentage']);self.assertNotIn('ranking',[p['value'] for p in formats]);self.assertIn('ranking',[p['value'] for p in projection['creative_signals']['weak_patterns']])
        self.assertEqual('cold_start',projection['learning']['stage']);self.assertNotIn('tone_performance',summary);self.assertIn('format_performance',summary);self.assertLessEqual(len(json.dumps(projection).encode()),16000)

    def test_unknown_response_is_not_ranked_by_raw_views(self):
        lane=adapter(self);videos=[observation(cid=f'za-missing-fixture-{i:03}') for i in range(5)]
        for video in videos:video['metrics']={'views':1000000}
        with tempfile.TemporaryDirectory() as td:summary=analytics.build_summary(Path(td),{'collected_at':'2026-10-10T00:00:00Z','videos':videos},profile=lane.PROFILE)
        self.assertEqual([],lane.planner_projection(summary)['creative_signals']['supported_patterns'])

    def test_collection_reuses_reporting_and_retention_with_isolated_outputs(self):
        lane=adapter(self)
        from test_analytics_reporting import FakeReportingApi
        reporting=FakeReportingApi()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);planner=root/'planner';warehouse=root/'warehouse';fixture(planner)
            def data(path,params,token):
                if path=='channels':return {'items':[{'id':CHANNEL,'snippet':{'customUrl':'@WackyAstrology'}}]}
                return {'items':[{'id':VIDEO,'statistics':{'viewCount':'100'},'contentDetails':{'duration':'PT6S'}}]}
            def context(path):
                target=path/'content/context.json';target.write_text(json.dumps({'analytics_summary':json.loads((path/'content/planner-analytics.json').read_text())}));return target
            with patch('analytics.access_token',return_value='token'),patch('analytics.youtube_data',side_effect=data),patch('analytics.collect_optional_analytics_reports',return_value=({},[])),patch('analytics.collect_per_video_traffic_sources',return_value=({},[])),patch('analytics.analytics_report_all',return_value={}),patch('analytics.analytics_report',return_value={}),patch('analytics.reporting_json',side_effect=lambda method,path,token,body=None:reporting.json(method,path,body)),patch('analytics.reporting_bytes',side_effect=lambda url,token:reporting.bytes(url)),patch('analytics.rebuild_planner_context',side_effect=context):
                outputs=lane.run(planner,warehouse,NOW)
            self.assertTrue(list((warehouse/'raw/basic').glob('**/*.csv')))
            state=json.loads((warehouse/'manifest/collection-state.json').read_text())
            self.assertEqual(CHANNEL,state['channel_id']);self.assertTrue(state['retention_attempts'])
            self.assertEqual('wacky-astrology-youtube-analytics',json.loads((warehouse/'manifest/schema-version.json').read_text())['schema'])
            index=json.loads((planner/'content/analytics-index.json').read_text())
            self.assertEqual('skyfremen/zodiac-analytics-data',index['analytics_repository'])
            self.assertEqual('zodiac',json.loads((planner/'content/context.json').read_text())['analytics_summary']['lane'])
            for path in outputs.planner_files:self.assertTrue(analytics_remote.allowed_path('planner',path.relative_to(planner).as_posix()))
            for path in outputs.warehouse_files:self.assertTrue(analytics_remote.allowed_path('warehouse',path.relative_to(warehouse).as_posix()))

    def test_warehouse_channel_mismatch_fails_before_reporting(self):
        lane=adapter(self)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);planner=root/'planner';warehouse=root/'warehouse';fixture(planner)
            path=warehouse/'manifest/collection-state.json';path.parent.mkdir(parents=True);path.write_text(json.dumps({'channel_id':'UC'+'b'*22}))
            with patch('analytics.access_token',return_value='token'),patch('analytics.youtube_data',return_value={'items':[{'id':CHANNEL,'snippet':{'customUrl':'@WackyAstrology'}}]}),patch('analytics.snapshot',return_value={'collected_at':'2026-10-10T00:00:00Z','videos':[]}),patch('analytics.sync_reporting',side_effect=AssertionError('Reporting reached before channel guard')) as reporting:
                with self.assertRaisesRegex(RuntimeError,'warehouse channel'):lane.run(planner,warehouse,NOW)
                reporting.assert_not_called()

    def test_retention_excludes_curves_outside_current_eligible_videos(self):
        lane=adapter(self)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for video,watch in ((VIDEO,.6),('xxxxxxxxxxx',.99)):
                path=root/f'retention/{video}/72h.json';path.parent.mkdir(parents=True)
                path.write_text(json.dumps({'video_id':video,'column_headers':[{'name':'elapsedVideoTimeRatio'},{'name':'audienceWatchRatio'}],'rows':[[.05,watch],[.5,watch],[.95,watch]]}))
            summary=analytics.build_summary(root,{'collected_at':'2026-10-10T00:00:00Z','videos':[observation()]},profile=lane.PROFILE)
            self.assertEqual(1,summary['retention_patterns']['curve_count'])
            self.assertEqual(.6,summary['retention_patterns']['median_opening_retention'])

    def test_zodiac_source_report_keeps_missing_engagement_unknown(self):
        adapter(self)
        def report(params,token):
            return {'columnHeaders':[{'name':key} for key in ('video','insightTrafficSourceType','views','engagedViews')],'rows':[[VIDEO,'SHORTS',100,None]]}
        values,_=analytics.collect_per_video_traffic_sources([{'youtube_video_id':VIDEO}],'token',NOW,report,preserve_nulls=True)
        self.assertIsNone(values[VIDEO]['shorts_source_engaged_views'])
        self.assertIsNone(values[VIDEO]['shorts_source_engaged_view_rate_percentage'])
        current={'collected_at':'2026-10-10T00:00:00Z','videos':[],'analytics_reports':{'geography':{'rows':[{'country':'US','views':100,'engagedViews':None}]}}}
        with tempfile.TemporaryDirectory() as td:summary=analytics.build_summary(Path(td),current,profile=adapter(self).PROFILE)
        self.assertIsNone(summary['geography_analysis']['top'][0].get('engaged_view_rate_percentage'))

    def test_remote_race_reloads_and_preserves_concurrent_planner_input(self):
        from types import SimpleNamespace
        class Repo:
            def __init__(self,role):self.role=role;self.generation=1;self.files={'input.json':'1'} if role=='planner' else {};self.race=role=='planner'
            def head_sha(self):return str(self.generation)*40
            def download_tree(self,sha,work):
                root=work/self.role;root.mkdir()
                for path,content in self.files.items():
                    target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content)
                return root
            def commit_files(self,sha,root,paths):
                if self.race:
                    self.race=False;self.generation+=1;self.files['input.json']='2';raise analytics_remote.RefAdvanced('planner race')
                for path in paths:
                    relative=path.relative_to(root).as_posix()
                    if not analytics_remote.allowed_path(self.role,relative):raise AssertionError(relative)
                    self.files[relative]=path.read_text()
                self.generation+=1;return self.head_sha()
        planner=Repo('planner');warehouse=Repo('warehouse');calls=[]
        def runner(private,raw,now):
            value=(private/'input.json').read_text();calls.append(value)
            context=private/'content/context.json';context.parent.mkdir();context.write_text(json.dumps({'preserved_input':value}))
            state=raw/'manifest/collection-state.json';state.parent.mkdir(exist_ok=True);state.write_text('{}')
            return SimpleNamespace(planner_files=[context],warehouse_files=[state])
        analytics_remote.run_remote(planner,warehouse,runner)
        self.assertEqual(['1','2'],calls)
        self.assertEqual('2',json.loads(planner.files['content/context.json'])['preserved_input'])
        self.assertEqual('2',planner.files['input.json'])

    def test_remote_never_falls_back_to_drama_secrets(self):
        lane=adapter(self)
        with patch.dict('os.environ',{'PRIVATE_STATE_TOKEN':'drama-token','PRIVATE_STATE_REPOSITORY':'skyfremen/youtube-workflow'},clear=True):
            with self.assertRaisesRegex(RuntimeError,'ZODIAC_STATE_TOKEN'):lane.remote_main()
        self.assertEqual('skyfremen/zodiac-analytics-data',lane.PROFILE.warehouse_repository);self.assertEqual(('ZODIAC_CLIENT_ID','ZODIAC_CLIENT_SECRET','ZODIAC_REFRESH_TOKEN'),lane.PROFILE.credential_names)
        self.assertFalse(analytics_remote.allowed_path('planner','PLANNING.md'))

if __name__=='__main__':unittest.main()
