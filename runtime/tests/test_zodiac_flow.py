"""The list flow uses only synthetic fixtures; never uploads real videos."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE = 'a'*40
RUNTIME = 'b'*40
NOW = datetime(2026,10,8,5,0,tzinfo=timezone.utc)
SIGNS = 'Aries Taurus Gemini Cancer Leo Virgo Libra Scorpio Sagittarius Capricorn Aquarius Pisces'.split()
ANSWERS = ['Double texts immediately','Goes silent for days','Starts another conversation','Rereads the whole chat','Posts a perfect selfie','Checks every last detail','Asks a mutual friend','Leaves you on read next','Forgets it ever happened','Deletes your number quietly','Vanishes into group chats','Invents a dramatic ending']

def fixture(count=1):
    winners=[]
    for i in range(count):
        winners.append({'id':f'za-flow-fixture-{i:03}', 'format':'sign_results',
            'title': ['HOW EACH SIGN ACTS WHEN LEFT ON READ','ZODIAC SIGNS WHO HOLD GRUDGES FOR TOO LONG'][i],
            'duration_seconds':6,'editorial_scores':{'hook':27,'curiosity':23,'emotion':18,'answers':14,'originality':9},
            'editorial_reason':'Synthetic relationship comparison with distinct behaviors for every sign.',
            'rows':[{'label':s,'answer':a if i==0 else f'Keeps reminder number {j}'} for j,(s,a) in enumerate(zip(SIGNS,ANSWERS))]})
    return {'winner_count':count,'winners':winners}

def save(root, name, data):
    path=root/'content/drafts'/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data))
    return path

class FlowTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('zodiac.lifecycle'), 'shared lifecycle is missing')
        self.flow=importlib.import_module('zodiac.lifecycle')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.path=save(self.root,'draft-20261008T050000-abc12345.json',fixture(2))

    def finalize(self, path=None):
        return self.flow.finalize(self.root,path or self.path,now=NOW,source_sha=SOURCE,runtime_sha=RUNTIME)

    def test_finalize_creates_two_distinct_execution_slots_and_context(self):
        result=self.finalize()
        self.assertEqual(2,len(result['execution_ids']))
        request=json.loads((self.root/result['request_path']).read_text())
        self.assertEqual('skyfremen/zodiac-workflow',request['source_repository'])
        self.assertEqual(2,len({i['publish_at'] for i in request['items']}))
        self.assertFalse(request['publication']['enabled'])
        context=json.loads((self.root/'content/context.json').read_text())
        self.assertEqual(2,len(context['recent_content']))

    def test_rerun_is_idempotent(self):
        first=self.finalize(); second=self.finalize()
        self.assertEqual(first,second)
        self.assertEqual(1,len(list((self.root/'content/requests').glob('*.json'))))

    def test_bad_winner_creates_targeted_failure_without_request(self):
        data=fixture(2); data['winners'][1]['rows'].pop(); self.path.write_text(json.dumps(data))
        with self.assertRaises(self.flow.FlowRejected): self.finalize()
        failure=json.loads((self.root/'content/failures'/self.path.name).read_text())
        self.assertTrue(failure['repairable'])
        self.assertEqual([1],[x['winner_index'] for x in failure['affected_winners']])
        self.assertFalse(list((self.root/'content/requests').glob('*.json')))

    def test_compact_repair_preserves_unaffected_winner(self):
        original=fixture(2); bad=deepcopy(original); bad['winners'][1]['rows'].pop()
        self.path.write_text(json.dumps(bad))
        with self.assertRaises(self.flow.FlowRejected): self.finalize()
        repair=save(self.root,'draft-20261008T050100-def67890.json',{'winner_count':2,
            'supersedes_draft_id':self.path.stem,'replacements':[{'winner_index':1,'winner':original['winners'][1]}]})
        result=self.finalize(repair)
        request=json.loads((self.root/result['request_path']).read_text())
        self.assertEqual(original['winners'][0],request['items'][0]['winner'])

    def test_wrong_repair_indexes_rejected(self):
        bad=fixture(2); bad['winners'][1]['rows'].pop(); self.path.write_text(json.dumps(bad))
        with self.assertRaises(self.flow.FlowRejected): self.finalize()
        repair=save(self.root,'draft-20261008T050100-def67890.json',{'winner_count':2,'supersedes_draft_id':self.path.stem,
            'replacements':[{'winner_index':0,'winner':fixture(2)['winners'][0]}]})
        with self.assertRaisesRegex(self.flow.FlowRejected,'REPAIR_INDEX'): self.finalize(repair)

    def test_sixth_repair_is_rejected(self):
        bad=fixture(2); bad['winners'][1]['rows'].pop(); self.path.write_text(json.dumps(bad))
        with self.assertRaises(self.flow.FlowRejected): self.finalize()
        parent=self.path
        for i in range(1,7):
            repair=save(self.root,f'draft-20261008T050{i:03}-{i:08x}.json',{'winner_count':2,
                'supersedes_draft_id':parent.stem,'replacements':[{'winner_index':1,'winner':bad['winners'][1]}]})
            with self.assertRaisesRegex(self.flow.FlowRejected,'REPAIR_LIMIT' if i==6 else 'ROW_COUNT'):
                self.finalize(repair)
            parent=repair

    def test_scheduled_result_rejected_when_publication_disabled(self):
        batch=self.finalize(); eid=batch['execution_ids'][0]
        e=json.loads((self.root/f'content/executions/{eid}.json').read_text())
        outcome={**{k:e[k] for k in ('execution_id','content_id','request_id','request_blob_sha','runtime_sha')},
            'source_sha':SOURCE,'state':'scheduled','qc_passed':True,'artifact_name':eid,
            'video_sha256':'c'*64,'video_id':'abcdefghijk','publish_at':e['publish_at']}
        with self.assertRaisesRegex(self.flow.FlowRejected,'RESULT_PUBLICATION'):
            self.flow.ingest_result(self.root,outcome)

    def test_count_repair_uses_full_corrected_batch(self):
        bad=fixture(2); bad['winner_count']=1; self.path.write_text(json.dumps(bad))
        with self.assertRaises(self.flow.FlowRejected): self.finalize()
        repair=fixture(); repair['supersedes_draft_id']=self.path.stem
        path=save(self.root,'draft-20261008T050100-def67890.json',repair)
        self.assertEqual(1,len(self.finalize(path)['execution_ids']))

    def test_unrelated_request_rejected_when_ingesting_result(self):
        result=self.finalize(); eid=result['execution_ids'][0]
        with self.assertRaisesRegex(self.flow.FlowRejected,'RESULT'):
            self.flow.ingest_result(self.root,{'execution_id':eid,'request_id':'zq-'+'f'*24})

    def test_result_source_must_contain_the_exact_execution(self):
        import subprocess
        def git(*args):
            return subprocess.check_output(['git','-C',str(self.root),'-c','user.name=Fixture','-c','user.email=fixture@example.test',*args],text=True,stderr=subprocess.DEVNULL).strip()
        git('init','-b','main'); git('add','.'); git('commit','-m','draft fixture')
        draft_sha=git('rev-parse','HEAD')
        batch=self.flow.finalize(self.root,self.path,now=NOW,source_sha=draft_sha,runtime_sha=RUNTIME)
        git('add','.'); git('commit','-m','request fixture')
        eid=batch['execution_ids'][0]; e=json.loads((self.root/f'content/executions/{eid}.json').read_text())
        outcome={**{k:e[k] for k in ('execution_id','content_id','request_id','request_blob_sha','runtime_sha')},
            'source_sha':draft_sha,'state':'rendered','qc_passed':True,'artifact_name':eid,
            'video_sha256':'c'*64,'video_id':None,'publish_at':e['publish_at']}
        with self.assertRaisesRegex(self.flow.FlowRejected,'RESULT_SOURCE'):
            self.flow.ingest_result(self.root,outcome)

    def test_draft_path_traversal_rejected(self):
        other=self.root/'outside.json'; other.write_text(json.dumps(fixture()))
        with self.assertRaisesRegex(self.flow.FlowRejected,'DRAFT_PATH'): self.finalize(other)

    def test_result_keeps_request_provenance_and_updates_context(self):
        batch=self.finalize(); eid=batch['execution_ids'][0]
        execution=json.loads((self.root/f'content/executions/{eid}.json').read_text())
        outcome={**{k:execution[k] for k in ('execution_id','content_id','request_id','request_blob_sha','runtime_sha')},
            'source_sha':SOURCE,'state':'rendered','qc_passed':True,'artifact_name':eid,
            'video_sha256':'c'*64,'video_id':None,'publish_at':execution['publish_at']}
        self.flow.ingest_result(self.root,outcome)
        context=json.loads((self.root/'content/context.json').read_text())
        card=next(c for c in context['recent_content'] if c['id']==execution['content_id'])
        self.assertEqual('rendered',card['status'])

    def test_new_batch_never_reuses_reserved_slots(self):
        first=self.finalize()
        fresh=fixture(); fresh['winners'][0]['id']='za-new-unique-fixture'
        fresh['winners'][0]['title']='WHAT EACH SIGN REFUSES TO ADMIT AFTER AN ARGUMENT'
        fresh['winners'][0]['rows']=[{'label':s,'answer':f'Refuses confession number {i}'} for i,s in enumerate(SIGNS)]
        second=self.finalize(save(self.root,'draft-20261008T050100-def67890.json',fresh))
        old=json.loads((self.root/first['request_path']).read_text()); new=json.loads((self.root/second['request_path']).read_text())
        self.assertTrue(set(i['publish_at'] for i in old['items']).isdisjoint(i['publish_at'] for i in new['items']))

    def test_later_pending_draft_does_not_block_earlier_finalization(self):
        later=fixture(); later['winners'][0]['id']='za-later-pending-fixture'
        path=save(self.root,'draft-20261008T050100-def67890.json',later)
        self.assertEqual(2,len(self.finalize()['execution_ids']))
        with self.assertRaisesRegex(self.flow.FlowRejected,'HISTORY_SIMILAR_TOPIC'):
            self.finalize(path)

    def test_malformed_legacy_draft_does_not_poison_new_intake(self):
        old=save(self.root,'draft-20261008T040000-12345678.json',{})
        old.write_text('{broken')
        self.assertEqual(2,len(self.finalize()['execution_ids']))

    def test_malformed_intake_records_nonrepairable_failure(self):
        self.path.write_text('{broken')
        with self.assertRaisesRegex(self.flow.FlowRejected,'INVALID_JSON'): self.finalize()
        failure=json.loads((self.root/'content/failures'/self.path.name).read_text())
        self.assertFalse(failure['repairable'])
        self.assertEqual('INVALID_JSON',failure['code'])

    def test_duplicate_repair_preserves_unrelated_middle_winner(self):
        data=fixture(2)
        duplicate=deepcopy(data['winners'][0]); duplicate['id']='za-duplicate-fixture'
        data['winners'].append(duplicate); data['winner_count']=3
        self.path.write_text(json.dumps(data))
        with self.assertRaises(self.flow.FlowRejected): self.finalize()
        failure=json.loads((self.root/'content/failures'/self.path.name).read_text())
        self.assertEqual([0,2],[w['winner_index'] for w in failure['affected_winners']])

if __name__=='__main__': unittest.main()
