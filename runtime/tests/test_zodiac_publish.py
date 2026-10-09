"""Upload boundaries use synthetic local state and a fake YouTube client."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from test_zodiac_flow import prepare_execution, SOURCE, RUNTIME

CID='UC'+'z'*22
VIDEO='abcdefghijk'

class MemoryState:
    def __init__(self): self.records={}; self.fresh=True
    def load(self,path):
        return SimpleNamespace(data=deepcopy(self.records[path])) if path in self.records else None
    def create(self,path,data):
        if path in self.records:
            if self.records[path]!=data: raise RuntimeError('Immutable collision')
            return SimpleNamespace(data=deepcopy(data),created=False)
        self.records[path]=deepcopy(data)
        return SimpleNamespace(data=deepcopy(data),created=self.fresh)

class FakeYouTube:
    def __init__(self): self.inserts=0; self.body=None; self.remote=[]; self.fail_insert=False
    def channels(self): return self
    def playlistItems(self): return self
    def videos(self): return self
    def list(self,**kwargs):
        if kwargs.get('mine'):
            data={'items':[{'id':CID,'snippet':{'customUrl':'@WackyAstrology'},'contentDetails':{'relatedPlaylists':{'uploads':'UU-fixture'}}}]}
        elif kwargs.get('playlistId'):
            data={'items':[{'contentDetails':{'videoId':v['id']}} for v in self.remote]}
        else:
            ids=kwargs['id'].split(','); data={'items':[v for v in self.remote if v['id'] in ids]}
        return SimpleNamespace(execute=lambda:deepcopy(data))
    def insert(self,**kwargs):
        self.inserts+=1; self.body=deepcopy(kwargs['body'])
        def next_chunk(**ignored):
            self.remote=[{'id':VIDEO,'snippet':{**self.body['snippet'],'channelId':CID},
                'status':{**self.body['status'],'uploadStatus':'processed'}}]
            if self.fail_insert: raise TimeoutError('Upload outcome uncertain')
            return None,{'id':VIDEO}
        return SimpleNamespace(next_chunk=next_chunk)

class PublishingTests(unittest.TestCase):
    def setUp(self):
        import zodiac.publish as publisher
        self.publisher=publisher
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.request,self.item,self.execution=prepare_execution(self.root)
        self.request['publication']={'enabled':True,'channel_id':CID}
        self.item['publication']={'mode':'scheduled','publish_at':(datetime.now(timezone.utc)+timedelta(days=2)).replace(microsecond=0).isoformat().replace('+00:00','Z')}
        self.request['items']=[self.item]
        self.identity=publisher.identity_for(self.execution)
        self.state=MemoryState(); self.youtube=FakeYouTube()
        self.video=self.root/'fixture.mp4'; self.video.write_bytes(b'synthetic-no-video')
        self.result={**{k:self.execution[k] for k in ('execution_id','content_id','request_id','request_blob_sha','item_blob_sha')},
            'result_version':2,'status':'rendered','visibility':'private','verified':True,'qc_passed':True,'youtube_video_id':None,
            'runtime_sha':RUNTIME,'source_sha':SOURCE,'artifact_name':self.execution['execution_id'],
            'video_sha256':sha256(self.video.read_bytes()).hexdigest(),'publish_at':self.item['publication']['publish_at']}

    def upload(self):
        with patch('zodiac.publish.media_upload',return_value=object()):
            return self.publisher.upload(self.state,self.youtube,self.request,self.item,self.identity,self.video,self.result)

    def test_new_upload_is_private_scheduled_and_fenced_before_insert(self):
        evidence=self.upload()
        self.assertEqual(1,self.youtube.inserts)
        self.assertIn(self.publisher.evidence_path(self.item['content_id'],'intent'),self.state.records)
        self.assertEqual(VIDEO,evidence['youtube_video_id'])
        self.assertEqual('private',self.youtube.body['status']['privacyStatus'])
        self.assertEqual(self.item['publication']['publish_at'],self.youtube.body['status']['publishAt'])
        self.assertIn(self.publisher.marker(self.item['content_id']),self.youtube.body['snippet']['tags'])
        self.assertEqual(VIDEO,self.upload()['youtube_video_id'])
        self.assertEqual(1,self.youtube.inserts)

    def test_uncertain_insert_recovers_marker_without_another_upload(self):
        self.youtube.fail_insert=True
        with self.assertRaises(TimeoutError): self.upload()
        self.youtube.fail_insert=False
        recovered=self.publisher.recover(self.state,self.youtube,self.identity,self.request)
        self.assertEqual(VIDEO,recovered['youtube_video_id']); self.assertEqual(1,self.youtube.inserts)

    def test_unresolved_intent_blocks_reinsert(self):
        self.upload(); self.youtube.remote=[]
        del self.state.records[self.publisher.evidence_path(self.item['content_id'],'upload')]
        with self.assertRaisesRegex(self.publisher.RecoveryBlocked,'ambiguous'): self.upload()
        self.assertEqual(1,self.youtube.inserts)

    def test_intent_not_freshly_created_cannot_upload(self):
        self.state.fresh=False
        with self.assertRaisesRegex(self.publisher.RecoveryBlocked,'freshly'): self.upload()
        self.assertEqual(0,self.youtube.inserts)

    def test_failed_qc_or_changed_video_cannot_create_an_intent(self):
        self.result['qc_passed']=False
        with self.assertRaises(self.publisher.RecoveryBlocked): self.upload()
        self.assertEqual({},self.state.records)
        self.result['qc_passed']=True; self.video.write_bytes(b'changed')
        with self.assertRaises(self.publisher.RecoveryBlocked): self.upload()
        self.assertEqual({},self.state.records)

    def test_old_slot_cannot_be_silently_published_now(self):
        self.item['publication']['publish_at']='2020-01-01T00:00:00Z'
        self.result['publish_at']=self.item['publication']['publish_at']
        with self.assertRaisesRegex(self.publisher.RecoveryBlocked,'future'): self.upload()
        self.assertEqual(0,self.youtube.inserts)

    def test_verification_checks_processing_metadata_schedule_and_channel(self):
        evidence=self.upload()
        result=self.publisher.verify(self.youtube,self.request,self.item,self.identity,evidence,self.result,sleep=lambda _:None)
        self.assertEqual('scheduled',result['status']); self.assertTrue(result['verified'])
        self.youtube.remote[0]['status']['publishAt']='2030-01-01T00:00:00Z'
        with self.assertRaisesRegex(self.publisher.RecoveryBlocked,'schedule'):
            self.publisher.verify(self.youtube,self.request,self.item,self.identity,evidence,self.result,sleep=lambda _:None)

    def test_processing_upload_never_gets_a_success_result(self):
        evidence=self.upload(); self.youtube.remote[0]['status']['uploadStatus']='uploaded'
        with self.assertRaisesRegex(self.publisher.RecoveryBlocked,'pending'):
            self.publisher.verify(self.youtube,self.request,self.item,self.identity,evidence,self.result,sleep=lambda _:None)
        self.assertEqual(1,self.youtube.inserts)

    def test_wrong_channel_blocked_before_insert(self):
        self.request['publication']['channel_id']='UC'+'d'*22
        with self.assertRaisesRegex(self.publisher.RecoveryBlocked,'channel'): self.upload()
        self.assertEqual({},self.state.records)

    def test_evidence_cannot_be_used_for_a_different_execution(self):
        self.upload(); self.identity['execution_id']='ex-'+'f'*24
        with self.assertRaisesRegex(self.publisher.RecoveryBlocked,'identity'): self.upload()
        self.assertEqual(1,self.youtube.inserts)

    def test_zodiac_state_reuses_shared_writer_with_zodiac_only_scope(self):
        from output.state import GitHubState
        self.assertTrue(issubclass(self.publisher.ZodiacState,GitHubState))
        with patch.dict('os.environ',{'ZODIAC_STATE_TOKEN':'fixture'},clear=True):
            state=self.publisher.ZodiacState()
        self.assertEqual('skyfremen/zodiac-workflow',state.repo)
        for path in ('data/history.json','content/results/wd-'+'a'*24+'.json'):
            with self.assertRaises(self.publisher.RecoveryBlocked): state.allowed(path)

if __name__=='__main__': unittest.main()
