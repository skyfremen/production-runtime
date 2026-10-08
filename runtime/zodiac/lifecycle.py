"""Deterministic private Zodiac state; shared implementation on runtime/main."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha1, sha256
import json
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo
from .content import Rejected, read_json, validate

REPOSITORY='skyfremen/zodiac-workflow'
SHA=re.compile(r'^[0-9a-f]{40}$')
DRAFT=re.compile(r'^draft-[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$')
EXECUTION=re.compile(r'^ze-[0-9a-f]{24}$')
DEFAULT_CONFIG={'timezone':'Asia/Singapore','slots':['08:00','20:00'],
                'publication':{'enabled':False,'channel_id':None}}

class FlowRejected(ValueError): pass

def require(ok, reason):
    if not ok: raise FlowRejected(reason)

def encoded(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()

def blob(raw): return sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest()
def digest(value): return sha256(encoded(value)).hexdigest()

def immutable(path, value):
    path=Path(path); raw=encoded(value)
    if path.exists():
        require(path.read_bytes()==raw,'IMMUTABLE_STATE_CHANGED: '+path.name)
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as file: file.write(raw)

def load(path):
    try: return read_json(path)
    except (Rejected,OSError) as exc: raise FlowRejected(str(exc)) from None

def configuration(root):
    path=Path(root)/'content/config.json'
    cfg=load(path) if path.exists() else deepcopy(DEFAULT_CONFIG)
    require(type(cfg) is dict and set(cfg)==set(DEFAULT_CONFIG),'CONFIG_SCHEMA')
    try: ZoneInfo(cfg['timezone'])
    except (KeyError,TypeError): raise FlowRejected('CONFIG_TIMEZONE') from None
    slots=cfg['slots']; pub=cfg['publication']
    require(type(slots) is list and 1<=len(slots)<=24 and len(slots)==len(set(slots)) and
        all(isinstance(s,str) and re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',s) for s in slots),'CONFIG_SLOTS')
    require(type(pub) is dict and set(pub)=={'enabled','channel_id'} and type(pub['enabled']) is bool,'PUBLICATION_SCHEMA')
    if pub['enabled']:
        require(isinstance(pub['channel_id'],str) and re.fullmatch(r'UC[A-Za-z0-9_-]{22}',pub['channel_id']) and
            pub['channel_id']!='UCvrq2m9G4yrwPfL_X-QPzMA','ZODIAC_CHANNEL_REQUIRED')
    else: require(pub['channel_id'] is None or isinstance(pub['channel_id'],str),'PUBLICATION_CHANNEL')
    return cfg

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

def previous(root,excluded):
    result=[]
    # Approved requests hold reconstructed winners, including successful repairs.
    for path in sorted((Path(root)/'content/requests').glob('zq-*.json')):
        request=load(path)
        if request.get('draft_id') not in excluded:
            result.extend(item['winner'] for item in request['items'])
    # Include older valid drafts that predate this flow, without double-counting.
    ids={w['id'] for w in result}
    for path in sorted((Path(root)/'content/drafts').glob('draft-*.json')):
        # Later unfinalized submissions must not retroactively invalidate this
        # draft. Approved requests above always remain authoritative history.
        if excluded and path.stem>=min(excluded): continue
        if path.stem in excluded or (Path(root)/'content/failures'/path.name).exists(): continue
        try:
            data=load(path)
            if isinstance(data,dict) and 'supersedes_draft_id' in data: continue
            validate(data)
        except (FlowRejected,Rejected,KeyError,TypeError): continue
        for w in data['winners']:
            if w['id'] not in ids: result.append(w); ids.add(w['id'])
    return result

def check_draft(root,path):
    try: data,ancestry=reconstruct(root,path)
    except FlowRejected as exc:
        immutable(Path(root)/'content/failures'/path.name,{
            'draft_id':path.stem,'code':str(exc),'repairable':False,'affected_winners':[]})
        raise
    old=previous(root,ancestry)
    affected=[]
    try:
        validate(data,old)
        return data
    except (Rejected,KeyError,TypeError) as exc:
        code=str(exc); winners=data.get('winners',[]); n=data.get('winner_count')
        if type(winners) is list and type(n) is int and 1<=n<=10 and len(winners)==n:
            for i,w in enumerate(winners):
                try: validate({'winner_count':1,'winners':[w]},old)
                except (Rejected,KeyError,TypeError) as err:
                    affected.append({'winner_index':i,'winner':w,'violations':[str(err)]})
            # Reuse the validator on clean pairs to isolate actual conflicts,
            # leaving unrelated winners out of the repair envelope.
            invalid={w['winner_index'] for w in affected}
            conflicts={}
            for i in range(len(winners)):
                for j in range(i+1,len(winners)):
                    if i in invalid or j in invalid: continue
                    try: validate({'winner_count':2,'winners':[winners[i],winners[j]]})
                    except Rejected as err:
                        for index in (i,j): conflicts.setdefault(index,[]).append(str(err))
            affected.extend({'winner_index':i,'winner':winners[i],'violations':errors}
                for i,errors in conflicts.items())
            affected.sort(key=lambda w:w['winner_index'])
        count_error=code=='WINNER_COUNT_MISMATCH' and type(n) is int and 1<=n<=10 and type(winners) is list
        failure={'draft_id':path.stem,'code':code,'repairable':bool(affected) or count_error,'affected_winners':affected}
        if count_error: failure['all_winners']=winners
        immutable(Path(root)/'content/failures'/path.name,failure)
        raise FlowRejected(code) from None

def allocate(root,count,cfg,now):
    require(now.tzinfo is not None,'TIMEZONE_REQUIRED')
    used={item['publish_at'] for p in (Path(root)/'content/requests').glob('zq-*.json') for item in load(p)['items']}
    local=(now+timedelta(minutes=15)).astimezone(ZoneInfo(cfg['timezone'])); choices=[]
    for day in range(366):
        date=local.date()+timedelta(days=day)
        for slot in sorted(cfg['slots']):
            hour,minute=map(int,slot.split(':'))
            candidate=datetime(date.year,date.month,date.day,hour,minute,tzinfo=local.tzinfo)
            utc=candidate.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
            if candidate>=local and utc not in used: choices.append(utc)
            if len(choices)==count: return choices
    raise FlowRejected('SLOTS_EXHAUSTED')

def finalize(root,path,*,now=None,source_sha,runtime_sha):
    root=Path(root).resolve(); path=draft_path(root,path)
    require(SHA.fullmatch(source_sha) and SHA.fullmatch(runtime_sha),'SOURCE_SHA')
    # A rerun keeps its original runtime revision and reserved slots.
    existing=[load(p) for p in (root/'content/requests').glob('zq-*.json') if load(p).get('draft_id')==path.stem]
    if existing:
        require(len(existing)==1,'DUPLICATE_DRAFT_REQUEST')
        req=existing[0]
        require(req['draft_blob_sha']==blob(path.read_bytes()),'DRAFT_IMMUTABILITY')
        return {'request_path':f"content/requests/{req['request_id']}.json",'execution_ids':[i['execution_id'] for i in req['items']], 'runtime_sha':req['runtime_sha']}
    data=check_draft(root,path); cfg=configuration(root)
    slots=allocate(root,data['winner_count'],cfg,now or datetime.now(timezone.utc))
    key={'draft_id':path.stem,'draft_blob_sha':blob(path.read_bytes()),'winners':data['winners'],'config':cfg}
    rid='zq-'+digest(key)[:24]
    items=[]
    for w,at in zip(data['winners'],slots):
        eid='ze-'+digest({'request_id':rid,'content_id':w['id']})[:24]
        items.append({'execution_id':eid,'content_id':w['id'],'publish_at':at,'winner':w})
    req={'request_version':1,'lane':'zodiac','source_repository':REPOSITORY,'request_id':rid,
         'draft_id':path.stem,'draft_blob_sha':key['draft_blob_sha'],'draft_source_sha':source_sha,
         'runtime_sha':runtime_sha,'publication':cfg['publication'],'items':items}
    request_path=f'content/requests/{rid}.json'
    immutable(root/request_path,req)
    for item in items:
        execution={'execution_version':1,'lane':'zodiac','source_repository':REPOSITORY,'state':'prepared',
            'execution_id':item['execution_id'],'content_id':item['content_id'],'publish_at':item['publish_at'],
            'request_id':rid,'request_path':request_path,'request_blob_sha':blob(encoded(req)),
            'item_sha256':digest(item),'runtime_sha':runtime_sha}
        immutable(root/f"content/executions/{item['execution_id']}.json",execution)
    build_context(root)
    return {'request_path':request_path,'execution_ids':[i['execution_id'] for i in items],'runtime_sha':runtime_sha}

def build_context(root):
    root=Path(root); by_id={}
    for path in sorted((root/'content/drafts').glob('draft-*.json')):
        if (root/'content/failures'/path.name).exists(): continue
        try:
            draft,_=reconstruct(root,path); validate(draft)
        except (FlowRejected,Rejected,KeyError,TypeError): continue
        for w in draft['winners']:
            by_id[w['id']]={'id':w['id'],'draft_id':path.stem,'format':w['format'],'title':w['title'],'rows':w['rows'],'status':'draft'}
    for path in sorted((root/'content/requests').glob('zq-*.json')):
        for item in load(path)['items']:
            if item['content_id'] in by_id: by_id[item['content_id']].update(status='prepared',publish_at=item['publish_at'])
    for path in sorted((root/'content/results').glob('ze-*.json')):
        result=load(path)
        if result.get('content_id') in by_id: by_id[result['content_id']].update(status=result['state'])
    cards=sorted(by_id.values(),key=lambda x:(x['draft_id'],x['id']),reverse=True)[:40]
    context={'lane':'zodiac','recent_content':cards,'publication':configuration(root)['publication']}
    path=root/'content/context.json'; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(encoded(context))
    return context

def ingest_result(root,result):
    root=Path(root); eid=result.get('execution_id') if isinstance(result,dict) else None
    require(isinstance(eid,str) and EXECUTION.fullmatch(eid),'RESULT_EXECUTION')
    execution=load(root/f'content/executions/{eid}.json')
    require(set(result)=={'execution_id','content_id','request_id','request_blob_sha','runtime_sha','source_sha','state','qc_passed','artifact_name','video_sha256','video_id','publish_at'},'RESULT_SCHEMA')
    for key in ('execution_id','content_id','request_id','request_blob_sha','runtime_sha','publish_at'):
        require(result.get(key)==execution.get(key),'RESULT_PROVENANCE: '+key)
    require(result['qc_passed'] is True and result['state'] in ('rendered','scheduled') and SHA.fullmatch(result['source_sha']) and
        re.fullmatch(r'[0-9a-f]{64}',str(result['video_sha256'])) and result['artifact_name']==eid,'RESULT_INVALID')
    require((result['state']=='rendered' and result['video_id'] is None) or
        (result['state']=='scheduled' and re.fullmatch(r'[A-Za-z0-9_-]{11}',str(result['video_id']))),'RESULT_VIDEO')
    request=load(root/execution['request_path'])
    require(blob((root/execution['request_path']).read_bytes())==execution['request_blob_sha'],'RESULT_REQUEST_BLOB')
    require((result['state']=='scheduled')==request['publication']['enabled'],'RESULT_PUBLICATION')
    if (root/'.git').exists():
        try:
            subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',result['source_sha'],'HEAD'],check=True,capture_output=True)
            source_raw=subprocess.check_output(['git','-C',str(root),'show',result['source_sha']+f':content/executions/{eid}.json'],stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError: raise FlowRejected('RESULT_SOURCE_EXECUTION_MISSING') from None
        require(source_raw==(root/f'content/executions/{eid}.json').read_bytes(),'RESULT_SOURCE_EXECUTION_CHANGED')
    immutable(root/f'content/results/{eid}.json',result)
    build_context(root)
    return result

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument('action',choices=('finalize','context','ingest-result'))
    p.add_argument('--root',default='.'); p.add_argument('--draft'); p.add_argument('--source-sha'); p.add_argument('--runtime-sha'); p.add_argument('--result'); p.add_argument('--output')
    a=p.parse_args(argv)
    try:
        if a.action=='finalize': result=finalize(a.root,a.draft,source_sha=a.source_sha,runtime_sha=a.runtime_sha)
        elif a.action=='context': result=build_context(a.root)
        else: result=ingest_result(a.root,load(a.result))
        if a.output: Path(a.output).write_bytes(encoded(result))
        print(json.dumps(result,sort_keys=True)); return 0
    except (FlowRejected,Rejected,OSError,TypeError,KeyError) as exc:
        print('ZODIAC_FLOW_REJECTED: '+str(exc),file=sys.stderr); return 2

if __name__=='__main__': raise SystemExit(main())
