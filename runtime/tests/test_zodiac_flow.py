"""The list flow uses only synthetic fixtures; never uploads real videos."""
from copy import deepcopy
from datetime import datetime, timezone
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

def prepare_execution(root):
    from hashlib import sha256
    from zodiac.contract import blob, encoded
    did='draft-20261009T000000-abc12345'
    draft=root/f'content/drafts/{did}.json'; draft.parent.mkdir(parents=True)
    draft.write_bytes(encoded(fixture()))
    winner=fixture()['winners'][0]
    item={'request_version':2,'content_id':winner['id'],'source_draft_id':did,
        'channel':{'name':'Wacky Astrology','handle':'@WackyAstrology'},'zodiac':winner,
        'publication':{'mode':'artifact','publish_at':'2026-10-09T01:00:00Z'},'visibility':'private',
        'render':{'width':1080,'height':1920,'fps':30,'duration_seconds':6,'video_codec':'h264','audio_codec':'aac','audio_sample_rate':48000},
        'youtube':{'title':winner['title'],'description':'A playful Zodiac comparison.','hashtags':['#Shorts'],'made_for_kids':False}}
    rid='rq-'+'d'*24
    request={'request_version':2,'request_id':rid,'source_draft_id':did,'draft_blob_sha':blob(draft.read_bytes()),
        'draft_source_sha':SOURCE,'publication':{'enabled':False,'channel_id':None},'items':[item]}
    request_path=root/f'content/requests/{rid}.json'; request_path.parent.mkdir(parents=True)
    request_path.write_bytes(encoded(request))
    rblob=blob(request_path.read_bytes()); iblob=blob(encoded(item))
    eid='ex-'+sha256(f"{rid}|{winner['id']}|{rblob}|{iblob}".encode()).hexdigest()[:24]
    execution={'execution_version':2,'execution_id':eid,'request_id':rid,'content_id':winner['id'],
        'request_path':f'content/requests/{rid}.json','request_source_sha':SOURCE,'request_blob_sha':rblob,'item_blob_sha':iblob,
        'contract_hash':sha256(b'zodiac-request-v2:channel,zodiac,publication,render,youtube;artifact-only').hexdigest(),
        'dispatch_id':'dp-'+sha256(eid.encode()).hexdigest()[:20],'state':'prepared'}
    path=root/f'content/executions/{eid}.json'; path.parent.mkdir(parents=True); path.write_bytes(encoded(execution))
    return request,item,execution


class ContentTests(unittest.TestCase):
    def test_current_fixture_has_four_format_contract(self):
        from zodiac.content import validate_submission
        self.assertEqual(2,validate_submission(fixture(2))['winner_count'])

