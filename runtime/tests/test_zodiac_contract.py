from pathlib import Path
import json
import tempfile
import unittest
from zodiac.production import load_execution, produce, ProductionRejected
from zodiac.contract import blob, encoded
from test_zodiac_flow import fixture, prepare_execution, SOURCE, RUNTIME
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

    def test_workflow_selects_run_revision(self):
        import os,textwrap
        from unittest.mock import patch
        workflow=(Path(__file__).resolve().parents[2]/'.github/workflows/zodiac.yml').read_text()
        self.assertNotIn('runtime_sha:',workflow)
        step=workflow.split('name: Fetch exact source without disallowed setup actions',1)[1]
        code=textwrap.dedent(step.split("python - <<'PY'\n",1)[1].split('\n          PY',1)[0])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); checkouts=[]
            def git(args,**kwargs):
                if args[1]=='init': (root/args[-1]/'.git').mkdir(parents=True,exist_ok=True)
                if 'checkout' in args: checkouts.append((args[2],args[-1]))
            env={'SOURCE_REPOSITORY':'skyfremen/zodiac-workflow','SOURCE_SHA':SOURCE,'STATE_TOKEN':'fixture','GITHUB_SHA':RUNTIME,'RUNTIME_SHA':RUNTIME}
            previous=Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ,env),patch('subprocess.run',side_effect=git): exec(code,{})
            finally: os.chdir(previous)
            self.assertEqual([('.state',SOURCE),('.runtime',RUNTIME)],checkouts)

    def test_catalogue_uses_only_current_filename(self):
        from zodiac.media import catalogue_paths
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'data').mkdir()
            (root/'data/background.json').write_text('obsolete')
            self.assertEqual('backgrounds.json',catalogue_paths(root)[0].name)

class CurrentContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.request,self.item,self.execution=prepare_execution(self.root)
        self.eid=self.execution['execution_id']; self.path=self.root/self.execution['request_path']
    def intake(self):
        return load_execution(self.root,self.eid,source_sha=SOURCE,runtime_sha=RUNTIME,repository='skyfremen/zodiac-workflow')
    def test_v2_intake_binds_common_envelope_to_zodiac_content(self):
        request,item,execution=self.intake()
        self.assertEqual(self.eid,execution['execution_id'])
        self.assertEqual(self.item['zodiac'],item['winner'])
    def test_unpinned_v2_accepts_each_run_revision_and_records_actual_revision(self):
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
        output=self.root/'output'
        def generate(*args,**kwargs):
            output.mkdir(exist_ok=True)
            (output/'manifest.json').write_text(json.dumps({'videos':[{'sha256':'d'*64}]}))
        from types import ModuleType
        renderer=ModuleType('zodiac.cards')
        renderer.generate=generate
        # This contract test mocks rendering and must run without media dependencies.
        with patch.dict('sys.modules',{'zodiac.cards':renderer}):
            result=produce(self.root,self.eid,source_sha=SOURCE,runtime_sha='c'*40,
                repository='skyfremen/zodiac-workflow',output=output)
        self.assertEqual('c'*40,result['runtime_sha'])
        self.assertEqual(self.eid,result['execution_id'])

    def test_pinned_execution_is_rejected_even_at_matching_revision(self):
        self.execution['runtime_sha']=RUNTIME
        (self.root/f'content/executions/{self.eid}.json').write_bytes(encoded(self.execution))
        with self.assertRaisesRegex(ProductionRejected,'EXECUTION_IDENTITY'):
            self.intake()

    def test_v2_tampered_request_is_rejected(self):
        self.request['items'][0]['zodiac']['title']='CHANGED TITLE DOES NOT MATCH ORIGINAL'
        self.path.write_bytes(encoded(self.request))
        with self.assertRaisesRegex(ProductionRejected,'REQUEST_BLOB'):
            self.intake()
    def test_transport_rejects_result_after_slot_abandonment(self):
        from zodiac.transport import record_result
        result={**{k:self.execution[k] for k in ('execution_id','content_id','request_id','request_blob_sha','item_blob_sha')},
            'result_version':2,'status':'rendered','visibility':'private','verified':True,'youtube_video_id':None,
            'runtime_sha':RUNTIME,'source_sha':SOURCE,'qc_passed':True,'artifact_name':self.eid,'video_sha256':'c'*64,'publish_at':self.item['publication']['publish_at']}
        path=self.root/f"content/abandonments/{self.item['content_id']}.json"; path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'abandonment_version':1,'content_id':self.item['content_id'],'request_id':self.request['request_id'],'reason':'production_abandoned'}))
        with self.assertRaisesRegex(ValueError,'ABANDONED'): record_result(self.root,result)
        self.assertFalse(list((self.root/'content/results').glob('*.json')))

if __name__=='__main__': unittest.main()

