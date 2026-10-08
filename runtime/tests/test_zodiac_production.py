from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_zodiac_flow import fixture, save, NOW, SOURCE, RUNTIME
from zodiac.lifecycle import finalize, load

class ProductionTests(unittest.TestCase):
    def test_production_is_runtime_owned_with_fixed_private_state(self):
        source=(Path(__file__).resolve().parents[2]/'.github/workflows/zodiac.yml').read_text()
        self.assertIn('workflow_dispatch:',source)
        self.assertNotIn('workflow_call:',source)
        self.assertIn("github.repository == 'skyfremen/production-runtime'",source)
        self.assertIn('SOURCE_REPOSITORY: skyfremen/zodiac-workflow',source)
        self.assertIn('repository: skyfremen/zodiac-workflow',source)
        self.assertIn('token: ${{ secrets.ZODIAC_STATE_TOKEN }}',source)
        self.assertIn('--repository "$SOURCE_REPOSITORY"',source)
        self.assertIn('uses: actions/upload-artifact@v4',source)
        self.assertNotIn('--repository "$GITHUB_REPOSITORY"',source)
        self.assertNotIn('secrets.RUNTIME_AUTH_',source)
        self.assertNotIn('secrets.PRIVATE_STATE_TOKEN',source)

    def test_artifact_can_be_replaced_on_result_write_retry(self):
        source=(Path(__file__).resolve().parents[2]/'.github/workflows/zodiac.yml').read_text()
        self.assertIn('overwrite: true',source)

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('zodiac.production'),'shared production entrypoint missing')
        self.prod=importlib.import_module('zodiac.production')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.path=save(self.root,'draft-20261008T050000-abc12345.json',fixture())
        self.batch=finalize(self.root,self.path,now=NOW,source_sha=SOURCE,runtime_sha=RUNTIME)
        self.eid=self.batch['execution_ids'][0]

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
        request['items'][0]['winner']['title']='ANOTHER TITLE WITH DIFFERENT ANSWERS'; path.write_text(json.dumps(request))
        with self.assertRaisesRegex(self.prod.ProductionRejected,'REQUEST_BLOB'): self.intake()

    def test_draft_changes_cannot_silently_change_production(self):
        data=fixture(); data['winners'][0]['rows'][0]['answer']='Changes the original promise'
        self.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(self.prod.ProductionRejected,'DRAFT_BLOB'): self.intake()

    def test_dramas_repository_cannot_call_zodiac(self):
        with self.assertRaisesRegex(self.prod.ProductionRejected,'REPOSITORY'): self.intake(repository='skyfremen/youtube-workflow')

    def test_wrong_runtime_revision_rejected(self):
        with self.assertRaisesRegex(self.prod.ProductionRejected,'RUNTIME'):
            self.prod.load_execution(self.root,self.eid,source_sha=SOURCE,runtime_sha='c'*40,repository='skyfremen/zodiac-workflow')

    def test_bad_execution_id_rejected_before_file_access(self):
        with self.assertRaisesRegex(self.prod.ProductionRejected,'EXECUTION_ID'):
            self.prod.load_execution(self.root,'../results/x',source_sha=SOURCE,runtime_sha=RUNTIME,repository='skyfremen/zodiac-workflow')

    def test_synthetic_end_to_end_preview_and_result(self):
        try: import PIL
        except ModuleNotFoundError: self.skipTest('Pillow media dependency absent')
        import shutil
        if not shutil.which('ffmpeg'): self.skipTest('ffmpeg absent')
        with patch('zodiac.publish.publish',side_effect=AssertionError('preview attempted YouTube access')):
            result=self.prod.produce(self.root,self.eid,source_sha=SOURCE,runtime_sha=RUNTIME,
                repository='skyfremen/zodiac-workflow',output=self.root/'artifact')
        self.assertEqual('rendered',result['state'])
        self.assertTrue(result['qc_passed'])
        self.assertIsNone(result['video_id'])
        self.assertTrue((self.root/'artifact/videos'/f"{result['content_id']}.mp4").is_file())
        from zodiac.lifecycle import ingest_result
        ingest_result(self.root,result)
        self.assertEqual('rendered',load(self.root/'content/context.json')['recent_content'][0]['status'])

class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('zodiac.publish'),'isolated publishing missing')
        self.pub=importlib.import_module('zodiac.publish')

    def test_wrong_or_wacky_channel_rejected(self):
        for ids in ([],['UCvrq2m9G4yrwPfL_X-QPzMA'],['UC'+'a'*22,'UC'+'b'*22]):
            with self.subTest(ids=ids),self.assertRaises(self.pub.PublishRejected):
                self.pub.check_channel({'items':[{'id':i} for i in ids]},'UC'+'a'*22)

    def test_matching_zodiac_channel_accepted(self):
        self.assertEqual('UC'+'a'*22,self.pub.check_channel({'items':[{'id':'UC'+'a'*22}]},'UC'+'a'*22))

    def test_existing_upload_reservation_never_allows_second_insert(self):
        state={'state':'reserved','video_id':None}
        with self.assertRaisesRegex(self.pub.PublishRejected,'RESERVED'):
            self.pub.upload_action(state)

    def test_uploaded_state_reuses_video_id(self):
        self.assertEqual('abcdefghijk',self.pub.upload_action({'state':'uploaded','video_id':'abcdefghijk'}))

if __name__=='__main__': unittest.main()
