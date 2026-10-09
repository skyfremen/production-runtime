from pathlib import Path
import json
import tempfile
import unittest
from zodiac.production import load_execution, produce, ProductionRejected
from zodiac.lifecycle import blob, encoded, digest
from test_zodiac_flow import fixture, SOURCE, RUNTIME
from hashlib import sha256

class RuntimeParityTests(unittest.TestCase):
    def test_container_matches_drama_without_install_steps(self):
        root=Path(__file__).resolve().parents[2]
        zodiac=(root/'.github/workflows/zodiac.yml').read_text()
        drama=(root/'.github/workflows/single.yml').read_text()
        import re
        image=re.search(r'image: (.+)',drama).group(1)
        self.assertIn('image: '+image,zodiac)
        self.assertNotIn('sudo apt-get',zodiac)
        self.assertIn('zodiac.transport',zodiac)
        self.assertIn('zodiac.core',zodiac)

    def test_workflow_selects_run_revision_or_preserves_existing_pin(self):
        import os
        import textwrap
        from unittest.mock import patch
        workflow=(Path(__file__).resolve().parents[2]/'.github/workflows/zodiac.yml').read_text()
        step=workflow.split('name: Fetch exact source without disallowed setup actions',1)[1]
        code=textwrap.dedent(step.split("python - <<'PY'\n",1)[1].split('\n          PY',1)[0])
        cases=[({'execution_version':2},'',SOURCE,None),
               ({'execution_version':2,'runtime_sha':RUNTIME},'',RUNTIME,None),
               ({'execution_version':1,'runtime_sha':RUNTIME},RUNTIME,RUNTIME,None),
               ({'execution_version':2},RUNTIME,None,'UNPINNED_EXECUTION_USES_RUN_REVISION'),
               ({'execution_version':1},'',None,'LEGACY_RUNTIME_PIN_REQUIRED'),
               ({'execution_version':2,'runtime_sha':RUNTIME},SOURCE,None,'RUNTIME_REVISION_MISMATCH')]
        for execution,supplied,expected,error in cases:
            with self.subTest(execution=execution,supplied=supplied),tempfile.TemporaryDirectory() as folder:
                root=Path(folder); eid='ex-'+'a'*24
                path=root/f'.state/content/executions/{eid}.json'; path.parent.mkdir(parents=True)
                path.write_text(json.dumps(execution)); checkouts=[]
                def git(args,**kwargs):
                    if args[1]=='init': (root/args[-1]/'.git').mkdir(parents=True,exist_ok=True)
                    if 'checkout' in args: checkouts.append((args[2],args[-1]))
                env={'SOURCE_REPOSITORY':'skyfremen/zodiac-workflow','SOURCE_SHA':SOURCE,'RUNTIME_SHA':supplied,
                     'EXECUTION_ID':eid,'STATE_TOKEN':'fixture','GITHUB_SHA':SOURCE,'GITHUB_ENV':str(root/'env')}
                previous=Path.cwd()
                try:
                    os.chdir(root)
                    with patch.dict(os.environ,env),patch('subprocess.run',side_effect=git):
                        if error:
                            with self.assertRaisesRegex(AssertionError,error): exec(code,{})
                        else:
                            exec(code,{})
                            self.assertIn(('.runtime',expected),checkouts)
                            self.assertEqual('RUNTIME_SHA='+expected+'\n',(root/'env').read_text())
                finally: os.chdir(previous)

    def test_plural_catalogue_is_preferred(self):
        from zodiac.media import catalogue_paths
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'data').mkdir()
            (root/'data/background.json').write_text('legacy')
            self.assertEqual('background.json',catalogue_paths(root)[0].name)
            (root/'data/backgrounds.json').write_text('current')
            self.assertEqual('backgrounds.json',catalogue_paths(root)[0].name)

class V2IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        did='draft-20261009T000000-abc12345'
        draft=self.root/f'content/drafts/{did}.json'; draft.parent.mkdir(parents=True)
        draft.write_bytes(encoded(fixture()))
        winner=fixture()['winners'][0]
        self.item={'request_version':2,'content_id':winner['id'],'source_draft_id':did,
            'channel':{'name':'Wacky Astrology','handle':'@WackyAstrology'},'zodiac':winner,
            'publication':{'mode':'artifact','publish_at':'2026-10-09T01:00:00Z'},'visibility':'private',
            'render':{'width':1080,'height':1920,'fps':30,'duration_seconds':6,'video_codec':'h264','audio_codec':'aac','audio_sample_rate':48000},
            'youtube':{'title':winner['title'],'description':'A playful Zodiac comparison.','hashtags':['#Shorts'],'made_for_kids':False}}
        rid='rq-'+'d'*24
        self.request={'request_version':2,'request_id':rid,'source_draft_id':did,'draft_blob_sha':blob(draft.read_bytes()),
            'draft_source_sha':SOURCE,'publication':{'enabled':False,'channel_id':None},'items':[self.item]}
        self.path=self.root/f'content/requests/{rid}.json'; self.path.parent.mkdir(parents=True)
        self.path.write_bytes(encoded(self.request))
        rblob=blob(self.path.read_bytes()); iblob=blob(encoded(self.item))
        self.eid='ex-'+sha256(f"{rid}|{winner['id']}|{rblob}|{iblob}".encode()).hexdigest()[:24]
        self.execution={'execution_version':2,'execution_id':self.eid,'request_id':rid,'content_id':winner['id'],
            'request_path':f'content/requests/{rid}.json','request_source_sha':SOURCE,'request_blob_sha':rblob,'item_blob_sha':iblob,
            'contract_hash':sha256(b'zodiac-request-v2:channel,zodiac,publication,render,youtube;artifact-only').hexdigest(),
            'dispatch_id':'dp-'+sha256(self.eid.encode()).hexdigest()[:20],'state':'prepared','runtime_sha':RUNTIME}
        path=self.root/f'content/executions/{self.eid}.json'; path.parent.mkdir(parents=True); path.write_bytes(encoded(self.execution))
    def intake(self):
        return load_execution(self.root,self.eid,source_sha=SOURCE,runtime_sha=RUNTIME,repository='skyfremen/zodiac-workflow')
    def test_v2_intake_binds_common_envelope_to_zodiac_content(self):
        request,item,execution=self.intake()
        self.assertEqual(self.eid,execution['execution_id'])
        self.assertEqual(self.item['zodiac'],item['winner'])
    def test_unpinned_v2_accepts_each_run_revision_and_records_actual_revision(self):
        self.execution.pop('runtime_sha')
        (self.root/f'content/executions/{self.eid}.json').write_bytes(encoded(self.execution))
        from zodiac.transport import record_result
        for revision in (RUNTIME,'c'*40):
            with self.subTest(revision=revision):
                _,_,execution=load_execution(self.root,self.eid,source_sha=SOURCE,runtime_sha=revision,repository='skyfremen/zodiac-workflow')
                self.assertNotIn('runtime_sha',execution)
        result={**{k:self.execution[k] for k in ('execution_id','content_id','request_id','request_blob_sha','item_blob_sha')},
            'result_version':2,'status':'rendered','visibility':'private','verified':True,'youtube_video_id':None,
            'runtime_sha':'c'*40,'source_sha':SOURCE,'qc_passed':True,'artifact_name':self.eid,
            'video_sha256':'d'*64,'publish_at':self.item['publication']['publish_at']}
        target=record_result(self.root,result)
        self.assertEqual('c'*40,json.loads(target.read_text())['runtime_sha'])
        # Completion remains immutable even if a later run selects another revision.
        changed={**result,'runtime_sha':RUNTIME}
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_STATE_CHANGED'):
            record_result(self.root,changed)

    def test_unpinned_production_result_uses_actual_run_revision(self):
        from unittest.mock import patch
        self.execution.pop('runtime_sha')
        (self.root/f'content/executions/{self.eid}.json').write_bytes(encoded(self.execution))
        output=self.root/'output'
        def generate(*args,**kwargs):
            output.mkdir(exist_ok=True)
            (output/'manifest.json').write_text(json.dumps({'videos':[{'sha256':'d'*64}]}))
        with patch('zodiac.cards.generate',side_effect=generate):
            result=produce(self.root,self.eid,source_sha=SOURCE,runtime_sha='c'*40,
                repository='skyfremen/zodiac-workflow',output=output)
        self.assertEqual('c'*40,result['runtime_sha'])
        self.assertEqual(self.eid,result['execution_id'])

    def test_pinned_v2_still_rejects_a_different_run_revision(self):
        with self.assertRaisesRegex(ProductionRejected,'RUNTIME_REVISION_MISMATCH'):
            load_execution(self.root,self.eid,source_sha=SOURCE,runtime_sha='c'*40,repository='skyfremen/zodiac-workflow')

    def test_v2_tampered_request_is_rejected(self):
        self.request['items'][0]['zodiac']['title']='CHANGED TITLE DOES NOT MATCH ORIGINAL'
        self.path.write_bytes(encoded(self.request))
        with self.assertRaisesRegex(ProductionRejected,'REQUEST_BLOB'):
            self.intake()
    def test_v2_end_to_end_artifact_result_and_transport(self):
        import shutil
        if not shutil.which('ffmpeg'): self.skipTest('ffmpeg absent')
        result=produce(self.root,self.eid,source_sha=SOURCE,runtime_sha=RUNTIME,repository='skyfremen/zodiac-workflow',output=self.root/'output')
        self.assertEqual(2,result['result_version'])
        self.assertEqual('rendered',result['status'])
        from zodiac.transport import record_result
        target=record_result(self.root,result)
        self.assertEqual(self.item['content_id']+'.json',target.name)
        self.assertFalse((self.root/'data/history.json').exists())

    def test_transport_rejects_result_after_slot_abandonment(self):
        from zodiac.transport import record_result
        result={**{k:self.execution[k] for k in ('execution_id','content_id','request_id','request_blob_sha','item_blob_sha','runtime_sha')},
            'result_version':2,'status':'rendered','visibility':'private','verified':True,'youtube_video_id':None,
            'source_sha':SOURCE,'qc_passed':True,'artifact_name':self.eid,'video_sha256':'c'*64,'publish_at':self.item['publication']['publish_at']}
        path=self.root/f"content/abandonments/{self.item['content_id']}.json"; path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'abandonment_version':1,'content_id':self.item['content_id'],'request_id':self.request['request_id'],'reason':'production_abandoned'}))
        with self.assertRaisesRegex(ValueError,'ABANDONED'): record_result(self.root,result)
        self.assertFalse(list((self.root/'content/results').glob('*.json')))

if __name__=='__main__': unittest.main()

