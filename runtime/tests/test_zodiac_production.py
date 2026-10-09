from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_zodiac_flow import fixture, prepare_execution, SOURCE, RUNTIME
from zodiac.contract import load

class ProductionTests(unittest.TestCase):
    def test_render_upload_and_remote_verification_precede_final_writeback(self):
        source=(Path(__file__).resolve().parents[2]/'.github/workflows/zodiac.yml').read_text()
        names=('Check Zodiac channel and recover an existing upload','Produce and verify exact Zodiac execution',
            'Upload verified Zodiac video and verify its schedule',
            'Record verified result on current main')
        positions=[source.index('- name: '+name) for name in names]
        self.assertEqual(sorted(positions),positions)
        for start,end in ((positions[0],positions[1]),(positions[2],positions[3])):
            step=source[start:end]
            for secret in ('ZODIAC_STATE_TOKEN','ZODIAC_CLIENT_ID','ZODIAC_CLIENT_SECRET','ZODIAC_REFRESH_TOKEN'):
                self.assertIn(secret+': ${{ secrets.'+secret+' }}',step)
        self.assertNotIn('ZODIAC_ARTIFACT_ONLY_PUBLICATION_DISABLED',source)
    def test_production_is_runtime_owned_with_fixed_private_state(self):
        source=(Path(__file__).resolve().parents[2]/'.github/workflows/zodiac.yml').read_text()
        self.assertIn('workflow_dispatch:',source)
        self.assertNotIn('workflow_call:',source)
        self.assertIn("github.repository == 'skyfremen/production-runtime'",source)
        self.assertIn('SOURCE_REPOSITORY: skyfremen/zodiac-workflow',source)
        self.assertIn('STATE_TOKEN: ${{ secrets.ZODIAC_STATE_TOKEN }}',source)
        self.assertIn('--repository "$SOURCE_REPOSITORY"',source)
        self.assertNotIn('uses: actions/upload-artifact@',source)
        self.assertNotIn('--repository "$GITHUB_REPOSITORY"',source)
        self.assertNotIn('secrets.RUNTIME_AUTH_',source)
        self.assertNotIn('secrets.PRIVATE_STATE_TOKEN',source)

    def test_workflow_respects_runtime_action_policy(self):
        import re
        source=(Path(__file__).resolve().parents[2]/'.github/workflows/zodiac.yml').read_text()
        actions=re.findall(r'uses:\s*(\S+)',source)
        self.assertEqual([],actions,'production uses the pinned container without Actions artifacts')
        self.assertNotIn('actions/checkout@',source)
        self.assertNotIn('actions/setup-python@',source)

    def test_mp4_is_not_uploaded_as_an_actions_artifact(self):
        source=(Path(__file__).resolve().parents[2]/'.github/workflows/zodiac.yml').read_text()
        self.assertNotIn('actions/upload-artifact@',source)
        self.assertNotIn('retention-days:',source)

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('zodiac.production'),'shared production entrypoint missing')
        self.prod=importlib.import_module('zodiac.production')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        request,_,execution=prepare_execution(self.root)
        self.batch={'request_path':execution['request_path']}
        self.path=self.root/f"content/drafts/{request['source_draft_id']}.json"
        self.eid=execution['execution_id']

    def intake(self,**kw):
        return self.prod.load_execution(self.root,self.eid,source_sha=SOURCE,runtime_sha=RUNTIME,
            repository=kw.pop('repository','skyfremen/zodiac-workflow'),**kw)

    def test_request_and_execution_identity_are_bound(self):
        request,item,execution=self.intake()
        self.assertEqual(execution['content_id'],item['winner']['id'])
        self.assertEqual(execution['request_id'],request['request_id'])

    def test_relative_checkout_root_matches_actions_layout(self):
        import os
        original=Path.cwd()
        try:
            os.chdir(self.root.parent)
            request,item,execution=self.prod.load_execution(self.root.name,self.eid,source_sha=SOURCE,
                runtime_sha=RUNTIME,repository='skyfremen/zodiac-workflow')
        finally: os.chdir(original)
        self.assertEqual(self.eid,execution['execution_id'])

    def test_workflow_passes_absolute_private_checkout_to_pinned_code(self):
        source=(Path(__file__).resolve().parents[2]/'.github/workflows/zodiac.yml').read_text()
        self.assertNotIn('--root .state',source)
        self.assertIn("load_execution(Path('.state').resolve()",source)

    def test_tampered_request_rejected(self):
        path=self.root/self.batch['request_path']; request=load(path)
        request['items'][0]['zodiac']['title']='ANOTHER TITLE WITH DIFFERENT ANSWERS'; path.write_text(json.dumps(request))
        with self.assertRaisesRegex(self.prod.ProductionRejected,'REQUEST_BLOB'): self.intake()

    def test_draft_changes_cannot_silently_change_production(self):
        data=fixture(); data['winners'][0]['rows'][0]['answer']='Changes the original promise'
        self.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(self.prod.ProductionRejected,'DRAFT_BLOB'): self.intake()

    def test_dramas_repository_cannot_call_zodiac(self):
        with self.assertRaisesRegex(self.prod.ProductionRejected,'REPOSITORY'): self.intake(repository='skyfremen/youtube-workflow')

    def test_each_run_can_select_current_runtime(self):
        _,_,execution=self.prod.load_execution(self.root,self.eid,source_sha=SOURCE,runtime_sha='c'*40,repository='skyfremen/zodiac-workflow')
        self.assertNotIn('runtime_sha',execution)

    def test_bad_execution_id_rejected_before_file_access(self):
        with self.assertRaisesRegex(self.prod.ProductionRejected,'EXECUTION_ID'):
            self.prod.load_execution(self.root,'../results/x',source_sha=SOURCE,runtime_sha=RUNTIME,repository='skyfremen/zodiac-workflow')


if __name__=='__main__': unittest.main()
