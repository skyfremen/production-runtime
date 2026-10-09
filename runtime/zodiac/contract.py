"""Runtime-owned current Zodiac intake contract. No planner/state construction lives here."""
from datetime import datetime
from hashlib import sha1, sha256
from pathlib import Path
import re
import json
from copy import deepcopy
import subprocess
from .content import validate_submission, read_json, Rejected
REPOSITORY='skyfremen/zodiac-workflow'
SHA=re.compile(r'^[0-9a-f]{40}$')
DRAFT=re.compile(r'^draft-[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$')

CONTRACT_HASH=sha256(b'zodiac-request-v2:channel,zodiac,publication,render,youtube;artifact-only').hexdigest()
CHANNEL={'name':'Wacky Astrology','handle':'@WackyAstrology'}
EX2=re.compile(r'^ex-[0-9a-f]{24}$')
RQ2=re.compile(r'^rq-[0-9a-f]{24}$')
RENDER={'width':1080,'height':1920,'fps':30,'duration_seconds':6,'video_codec':'h264','audio_codec':'aac','audio_sample_rate':48000}

class ContractRejected(ValueError): pass

def require(ok,reason):
    if not ok: raise ContractRejected(reason)


def encoded(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()

def blob(raw): return sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest()

def immutable(path, value):
    path=Path(path); raw=encoded(value)
    if path.exists():
        require(path.read_bytes()==raw,'IMMUTABLE_STATE_CHANGED: '+path.name)
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as file: file.write(raw)

def load(path):
    try: return read_json(path)
    except (Rejected,OSError) as exc: raise ContractRejected(str(exc)) from None

def draft_path(root,path):
    root=Path(root).resolve(); path=Path(path)
    if not path.is_absolute(): path=root/path
    require(path.resolve().parent==(root/'content/drafts').resolve() and path.suffix=='.json' and DRAFT.fullmatch(path.stem),'DRAFT_PATH')
    return path

def reconstruct(root,path,seen=None):
    path=draft_path(root,path); seen=set() if seen is None else seen
    require(path.stem not in seen,'REPAIR_CYCLE')
    seen.add(path.stem); require(len(seen)<=6,'REPAIR_LIMIT_EXCEEDED')
    data=load(path); require(type(data) is dict,'DRAFT_SCHEMA')
    if 'supersedes_draft_id' not in data: return data,seen
    parent=data['supersedes_draft_id']; require(isinstance(parent,str) and DRAFT.fullmatch(parent),'REPAIR_PARENT')
    failure_path=Path(root)/'content/failures'/f'{parent}.json'
    require(failure_path.is_file(),'REPAIR_FAILURE_MISSING')
    failure=load(failure_path); require(failure.get('repairable') is True,'REPAIR_NOT_ALLOWED')
    old,seen=reconstruct(root,Path('content/drafts')/(parent+'.json'),seen)
    require(data.get('winner_count')==old.get('winner_count'),'REPAIR_COUNT_CHANGED')
    if failure.get('code')=='WINNER_COUNT_MISMATCH':
        require(set(data)=={'winner_count','supersedes_draft_id','winners'},'COUNT_REPAIR_SCHEMA')
        return {'winner_count':data['winner_count'],'winners':data['winners']},seen
    require(set(data)=={'winner_count','supersedes_draft_id','replacements'},'COMPACT_REPAIR_SCHEMA')
    replacements=data['replacements']; require(type(replacements) is list,'REPAIR_INDEX')
    expected={w['winner_index'] for w in failure['affected_winners']}
    require(all(type(r) is dict and set(r)=={'winner_index','winner'} and type(r['winner_index']) is int for r in replacements),'REPAIR_INDEX')
    indexes=[r['winner_index'] for r in replacements]
    require(len(indexes)==len(set(indexes)) and set(indexes)==expected,'REPAIR_INDEX')
    merged=deepcopy(old)
    for r in replacements: merged['winners'][r['winner_index']]=r['winner']
    return merged,seen

def intake(root,eid,*,source_sha,runtime_sha):
    root=Path(root); execution=load(root/f'content/executions/{eid}.json')
    require(set(execution)=={'execution_version','execution_id','request_id','content_id','request_path','request_source_sha','request_blob_sha',
        'item_blob_sha','contract_hash','dispatch_id','state'} and execution['execution_version']==2 and execution['execution_id']==eid and
        execution['state']=='prepared' and execution['contract_hash']==CONTRACT_HASH,'EXECUTION_IDENTITY')
    rid=execution['request_id']; require(isinstance(rid,str) and RQ2.fullmatch(rid),'REQUEST_ID')
    require(execution['request_path']==f'content/requests/{rid}.json','REQUEST_PATH')
    require(SHA.fullmatch(str(execution['request_source_sha'])),'REQUEST_SOURCE_SHA')
    path=root/execution['request_path']; raw=path.read_bytes()
    require(blob(raw)==execution['request_blob_sha'],'REQUEST_BLOB_MISMATCH')
    request=load(path)
    require(set(request)=={'request_version','request_id','source_draft_id','draft_blob_sha','draft_source_sha','publication','items'} and
        request['request_version']==2 and request['request_id']==rid,'REQUEST_SCHEMA')
    publication=request['publication']
    require(type(publication) is dict and set(publication)=={'enabled','channel_id'} and type(publication['enabled']) is bool and
        (bool(re.fullmatch(r'UC[A-Za-z0-9_-]{22}',str(publication['channel_id']))) if publication['enabled'] else publication['channel_id'] is None),
        'REQUEST_PUBLICATION_CONFIGURATION')
    did=request['source_draft_id']; require(isinstance(did,str) and DRAFT.fullmatch(did),'REQUEST_DRAFT_ID')
    require(SHA.fullmatch(str(request['draft_source_sha'])),'DRAFT_SOURCE_SHA')
    draft=root/f'content/drafts/{did}.json'
    require(blob(draft.read_bytes())==request['draft_blob_sha'],'DRAFT_BLOB_MISMATCH')
    reconstructed,_=reconstruct(root,draft)
    items=request['items']; require(type(items) is list and len(items)>0,'REQUEST_ITEMS')
    winners=[]; slots=set()
    for item in items:
        require(type(item) is dict and set(item)=={'request_version','content_id','source_draft_id','channel','zodiac','publication','render','youtube','visibility'},'REQUEST_ITEM_SCHEMA')
        require(item['request_version']==2 and item['source_draft_id']==did and item['channel']==CHANNEL and item['render']==RENDER and
            item['visibility']=='private' and item['content_id']==item['zodiac'].get('id'),'REQUEST_ITEM_IDENTITY')
        require(type(item['youtube']) is dict and set(item['youtube'])=={'title','description','hashtags','made_for_kids'} and
            item['youtube']['title']==item['zodiac']['title'] and item['youtube']['made_for_kids'] is False,'REQUEST_YOUTUBE')
        pub=item['publication']; require(type(pub) is dict and set(pub)=={'mode','publish_at'} and
            pub['mode']==('scheduled' if publication['enabled'] else 'artifact'),'REQUEST_PUBLICATION')
        at=pub['publish_at']; require(isinstance(at,str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:00Z',at),'REQUEST_PUBLISH_AT')
        try: datetime.fromisoformat(at.replace('Z','+00:00'))
        except ValueError: raise ContractRejected('REQUEST_PUBLISH_AT') from None
        require(at not in slots,'DUPLICATE_SLOT'); slots.add(at); winners.append(item['zodiac'])
    validate_submission({'winner_count':len(winners),'winners':winners})
    require(reconstructed=={'winner_count':len(winners),'winners':winners},'DRAFT_REQUEST_MISMATCH')
    matches=[i for i in items if i['content_id']==execution['content_id']]
    require(len(matches)==1,'EXECUTION_ITEM_MISSING'); item=matches[0]
    require(blob(encoded(item))==execution['item_blob_sha'],'EXECUTION_ITEM_BLOB')
    expected='ex-'+sha256(f"{rid}|{item['content_id']}|{execution['request_blob_sha']}|{execution['item_blob_sha']}".encode()).hexdigest()[:24]
    require(eid==expected and execution['dispatch_id']=='dp-'+sha256(eid.encode()).hexdigest()[:20],'EXECUTION_FENCE_IDENTITY')
    if (root/'.git').exists():
        for sha,relative,expected_raw in (
            (source_sha,f'content/executions/{eid}.json',(root/f'content/executions/{eid}.json').read_bytes()),
            (execution['request_source_sha'],execution['request_path'],raw),
            (request['draft_source_sha'],f'content/drafts/{did}.json',draft.read_bytes())):
            try:
                subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',sha,'HEAD'],check=True,capture_output=True)
                actual=subprocess.check_output(['git','-C',str(root),'show',sha+':'+relative],stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError: raise ContractRejected('SOURCE_RECORD_MISSING') from None
            require(actual==expected_raw,'SOURCE_RECORD_CHANGED')
    return request,{**item,'winner':item['zodiac'],'publish_at':item['publication']['publish_at']},execution

