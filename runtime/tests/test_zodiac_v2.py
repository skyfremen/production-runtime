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
