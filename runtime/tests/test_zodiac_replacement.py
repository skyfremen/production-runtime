"""Deleted-upload replacement must preserve audit bytes and fail closed."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zodiac.contract import encoded, blob
from test_zodiac_flow import prepare_execution, SOURCE, RUNTIME
from test_zodiac_publish import FakeYouTube, MemoryState, CID, VIDEO
from zodiac import publish


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        request,item,execution=prepare_execution(self.root)
        request['publication']={'enabled':True,'channel_id':CID}
        item['publication']={'mode':'scheduled','publish_at':'2035-01-01T00:00:00Z'}
        request['items']=[item]
        (self.root/execution['request_path']).write_bytes(encoded(request))
        execution['request_blob_sha']=blob(encoded(request))
        execution['item_blob_sha']=blob(encoded(item))
        (self.root/f"content/executions/{execution['execution_id']}.json").unlink()
        execution['execution_id']='ex-'+sha256(f"{execution['request_id']}|{item['content_id']}|{execution['request_blob_sha']}|{execution['item_blob_sha']}".encode()).hexdigest()[:24]
        execution['dispatch_id']='dp-'+sha256(execution['execution_id'].encode()).hexdigest()[:20]
        (self.root/f"content/executions/{execution['execution_id']}.json").write_bytes(encoded(execution))
        self.request,self.item,self.execution=request,item,execution
        self.identity=publish.identity_for(execution)
        self.youtube=FakeYouTube(); state=MemoryState()
        video=self.root/'video.mp4'; video.write_bytes(b'fixture')
        rendered={**{k:execution[k] for k in ('execution_id','content_id','request_id','request_blob_sha','item_blob_sha')},
                  'result_version':2,'status':'rendered','visibility':'private','verified':True,'qc_passed':True,
                  'youtube_video_id':None,'runtime_sha':RUNTIME,'source_sha':SOURCE,
                  'artifact_name':execution['execution_id'],'video_sha256':sha256(b'fixture').hexdigest(),
                  'publish_at':item['publication']['publish_at']}
        with patch('zodiac.publish.media_upload',return_value=object()):
            record=publish.upload(state,self.youtube,request,item,self.identity,video,rendered)
        result=publish.verify(self.youtube,request,item,self.identity,record,rendered,sleep=lambda _:None)
        for path,data in state.records.items():
            target=self.root/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(encoded(data))
        self.result_path=f"content/results/{item['content_id']}.json"
        target=self.root/self.result_path; target.parent.mkdir(parents=True); target.write_bytes(encoded(result))
        self.auth={'replacement_version':1,'execution_id':execution['execution_id'],
                   'old_video_id':VIDEO,'confirmed_deleted':True,'reason':'User requested improved readability.'}
        self.path='content/replacements/replace-fixture.json'
        target=self.root/self.path; target.parent.mkdir(parents=True); target.write_bytes(encoded(self.auth))
        self.youtube.remote=[]

    def snapshot(self):
        return {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def prepare(self):
        from zodiac.replacement import prepare
        return prepare(self.root,self.path,self.youtube,source_sha=SOURCE,runtime_sha=RUNTIME)

    def test_verified_deletion_archives_exact_bytes_and_removes_only_three_active_files(self):
        before=self.snapshot(); out=self.prepare(); after=self.snapshot()
        archive=json.loads((self.root/out['archive_path']).read_text())
        expected={self.result_path,publish.evidence_path(self.item['content_id'],'intent'),
                  publish.evidence_path(self.item['content_id'],'upload')}
        self.assertEqual(expected,set(before)-set(after))
        self.assertEqual({out['archive_path']},set(after)-set(before))
        for path in expected:
            self.assertEqual(before[path],base64.b64decode(archive['files'][path]['base64']))
            self.assertEqual(blob(before[path]),archive['files'][path]['blob_sha'])
        for path in set(before)&set(after): self.assertEqual(before[path],after[path])
        self.assertEqual(self.auth,archive['authorization'])
        self.assertFalse(out['resumed'])

    def test_live_old_video_blocks_without_mutation(self):
        self.youtube.remote=[{'id':VIDEO,'snippet':{'channelId':CID}}]
        before=self.snapshot()
        with self.assertRaisesRegex(RuntimeError,'still exists'): self.prepare()
        self.assertEqual(before,self.snapshot())

    def test_other_marker_upload_blocks_without_mutation(self):
        self.youtube.remote=[{'id':'zyxwvutsrqp','snippet':{'channelId':CID,
                              'tags':[publish.marker(self.item['content_id'])]}}]
        before=self.snapshot()
        with self.assertRaisesRegex(RuntimeError,'marker'): self.prepare()
        self.assertEqual(before,self.snapshot())

    def test_api_error_blocks_without_mutation(self):
        before=self.snapshot()
        with patch.object(self.youtube,'list',side_effect=TimeoutError('API unavailable')):
            with self.assertRaises(TimeoutError): self.prepare()
        self.assertEqual(before,self.snapshot())

    def test_wrong_channel_blocks_without_mutation(self):
        before=self.snapshot()
        with patch('zodiac.publish.channel_for',side_effect=RuntimeError('wrong channel')):
            with self.assertRaisesRegex(RuntimeError,'channel'): self.prepare()
        self.assertEqual(before,self.snapshot())

    def test_tampered_evidence_blocks_without_mutation(self):
        path=self.root/publish.evidence_path(self.item['content_id'],'upload')
        doc=json.loads(path.read_text()); doc['youtube_video_id']='zyxwvutsrqp'; path.write_bytes(encoded(doc))
        before=self.snapshot()
        with self.assertRaisesRegex(RuntimeError,'evidence|mismatch'): self.prepare()
        self.assertEqual(before,self.snapshot())

    def test_authorization_for_another_video_blocks_without_mutation(self):
        self.auth['old_video_id']='zyxwvutsrqp'; (self.root/self.path).write_bytes(encoded(self.auth))
        before=self.snapshot()
        with self.assertRaisesRegex(RuntimeError,'old video'): self.prepare()
        self.assertEqual(before,self.snapshot())

    def test_replay_preserves_new_fences_and_result(self):
        first=self.prepare()
        for path in (self.result_path,publish.evidence_path(self.item['content_id'],'intent')):
            p=self.root/path; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b'new attempt protected')
        before=self.snapshot(); second=self.prepare()
        self.assertTrue(second['resumed']); self.assertEqual(first['archive_path'],second['archive_path'])
        self.assertEqual(before,self.snapshot())

    def test_replay_with_changed_authorization_blocks_without_mutation(self):
        self.prepare(); self.auth['reason']='changed authorization'
        (self.root/self.path).write_bytes(encoded(self.auth)); before=self.snapshot()
        with self.assertRaisesRegex(RuntimeError,'authorization'): self.prepare()
        self.assertEqual(before,self.snapshot())

    def test_request_path_cannot_escape_authorization_directory(self):
        self.path='../replace-fixture.json'; before=self.snapshot()
        with self.assertRaisesRegex(RuntimeError,'path'): self.prepare()
        self.assertEqual(before,self.snapshot())

    def test_active_execution_blocks(self):
        from zodiac.replacement import require_idle
        rows=[{'name':'Zodiac '+self.execution['execution_id'],'status':'in_progress'}]
        with self.assertRaisesRegex(RuntimeError,'active'): require_idle(rows,self.execution['execution_id'])
        require_idle([{'name':'Zodiac ex-other','status':'in_progress'}],self.execution['execution_id'])

    def test_expired_original_slot_blocks_without_mutation(self):
        before=self.snapshot()
        with patch('zodiac.replacement.datetime',wraps=datetime) as clock:
            clock.now.return_value=datetime(2040,1,1,tzinfo=timezone.utc)
            with self.assertRaisesRegex(RuntimeError,'future'): self.prepare()
        self.assertEqual(before,self.snapshot())

    def test_missing_upload_evidence_blocks_without_mutation(self):
        (self.root/publish.evidence_path(self.item['content_id'],'upload')).unlink()
        before=self.snapshot()
        with self.assertRaisesRegex(RuntimeError,'evidence missing'): self.prepare()
        self.assertEqual(before,self.snapshot())

    def test_incomplete_channel_scan_cannot_establish_no_matching_upload(self):
        before=self.snapshot(); original=self.youtube.list
        from types import SimpleNamespace
        def paginated(**kwargs):
            if kwargs.get('playlistId'):
                return SimpleNamespace(execute=lambda:{'items':[],'nextPageToken':'another-page'})
            return original(**kwargs)
        with patch.object(self.youtube,'list',side_effect=paginated):
            with self.assertRaisesRegex(RuntimeError,'incomplete'): self.prepare()
        self.assertEqual(before,self.snapshot())

    def test_dispatch_lock_identity_must_match_authorized_execution(self):
        from zodiac.replacement import authorization
        before=self.snapshot()
        with self.assertRaisesRegex(RuntimeError,'lock identity'):
            authorization(self.root,self.path,expected_execution_id='ex-'+'f'*24)
        self.assertEqual(before,self.snapshot())


if __name__=='__main__': unittest.main()
