"""Isolated exact-revision Zodiac production, called only by its private workflow."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import sys
from .content import validate, Rejected
from .lifecycle import REPOSITORY, SHA, EXECUTION, DRAFT, load, blob, digest, encoded, reconstruct, FlowRejected

class ProductionRejected(ValueError): pass

def require(ok,reason):
    if not ok: raise ProductionRejected(reason)

def load_execution(root,execution_id,*,source_sha,runtime_sha,repository):
    root=Path(root).resolve()
    require(repository==REPOSITORY,'ZODIAC_REPOSITORY_REQUIRED')
    require(isinstance(execution_id,str) and EXECUTION.fullmatch(execution_id),'EXECUTION_ID')
    require(isinstance(source_sha,str) and SHA.fullmatch(source_sha),'SOURCE_SHA')
    require(isinstance(runtime_sha,str) and SHA.fullmatch(runtime_sha),'RUNTIME_SHA')
    execution=load(root/f'content/executions/{execution_id}.json')
    require(execution.get('execution_id')==execution_id and execution.get('execution_version')==1 and
        execution.get('lane')=='zodiac' and execution.get('source_repository')==REPOSITORY and execution.get('state')=='prepared','EXECUTION_IDENTITY')
    require(execution.get('runtime_sha')==runtime_sha,'RUNTIME_REVISION_MISMATCH')
    rid=execution.get('request_id')
    require(isinstance(rid,str) and len(rid)==27 and rid.startswith('zq-') and all(c in '0123456789abcdef' for c in rid[3:]),'REQUEST_ID')
    require(execution.get('request_path')==f'content/requests/{rid}.json','REQUEST_PATH')
    path=root/execution['request_path']; raw=path.read_bytes()
    require(blob(raw)==execution.get('request_blob_sha'),'REQUEST_BLOB_MISMATCH')
    request=load(path)
    require(request.get('request_version')==1 and request.get('request_id')==rid and request.get('source_repository')==REPOSITORY and request.get('lane')=='zodiac','REQUEST_IDENTITY')
    require(request.get('runtime_sha')==runtime_sha,'REQUEST_RUNTIME_MISMATCH')
    items=request.get('items'); require(type(items) is list and 1<=len(items)<=10,'REQUEST_ITEMS')
    validate({'winner_count':len(items),'winners':[i['winner'] for i in items]})
    did=request.get('draft_id')
    require(isinstance(did,str) and DRAFT.fullmatch(did),'REQUEST_DRAFT_ID')
    draft_path=root/f'content/drafts/{did}.json'
    require(blob(draft_path.read_bytes())==request.get('draft_blob_sha'),'DRAFT_BLOB_MISMATCH')
    reconstructed,_=reconstruct(root,draft_path)
    require(reconstructed=={'winner_count':len(items),'winners':[i['winner'] for i in items]},'DRAFT_REQUEST_MISMATCH')
    matches=[i for i in items if i.get('execution_id')==execution_id]
    require(len(matches)==1,'EXECUTION_ITEM_MISSING')
    item=matches[0]
    require(digest(item)==execution.get('item_sha256') and item['content_id']==execution.get('content_id') and
        item['winner']['id']==item['content_id'] and item['publish_at']==execution.get('publish_at'),'EXECUTION_ITEM_MISMATCH')
    pub=request.get('publication')
    require(type(pub) is dict and set(pub)=={'enabled','channel_id'} and type(pub['enabled']) is bool,'PUBLICATION_SCHEMA')
    if pub['enabled']:
        from .publish import valid_channel_id
        require(valid_channel_id(pub['channel_id']),'ZODIAC_CHANNEL_REQUIRED')
    return request,item,execution

def produce(root,execution_id,*,source_sha,runtime_sha,repository,output):
    request,item,execution=load_execution(root,execution_id,source_sha=source_sha,runtime_sha=runtime_sha,repository=repository)
    from .cards import generate
    validated=validate({'winner_count':1,'winners':[item['winner']]})
    with tempfile.TemporaryDirectory(prefix='zodiac-intake-') as folder:
        path=Path(folder)/'validated.json'; path.write_bytes(encoded(validated))
        generate(path,output)
    manifest=load(Path(output)/'manifest.json'); video=manifest['videos'][0]
    result={k:execution[k] for k in ('execution_id','content_id','request_id','request_blob_sha','runtime_sha','publish_at')}
    result.update(source_sha=source_sha,state='rendered',qc_passed=True,artifact_name=execution_id,
                  video_sha256=video['sha256'],video_id=None)
    if request['publication']['enabled']:
        from .publish import publish
        result['video_id']=publish(request,item,Path(output)/video['file'],execution,source_sha)
        result['state']='scheduled'
        manifest['youtube_upload_enabled']=True
        Path(output,'manifest.json').write_bytes(encoded(manifest))
    Path(output,'result.json').write_bytes(encoded(result))
    return result

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument('--root',required=True); p.add_argument('--execution-id',required=True)
    p.add_argument('--source-sha',required=True); p.add_argument('--runtime-sha',required=True); p.add_argument('--repository',required=True); p.add_argument('--output',required=True)
    a=p.parse_args(argv)
    try:
        head=subprocess.check_output(['git','-C',a.root,'rev-parse','HEAD'],text=True).strip()
        require(head==a.source_sha,'PRIVATE_CHECKOUT_REVISION_MISMATCH')
        result=produce(a.root,a.execution_id,source_sha=a.source_sha,runtime_sha=a.runtime_sha,repository=a.repository,output=a.output)
        print(json.dumps({'execution_id':result['execution_id'],'state':result['state'],'qc_passed':True})); return 0
    except (ProductionRejected,FlowRejected,Rejected,ValueError,OSError,KeyError,TypeError,subprocess.CalledProcessError) as exc:
        print('ZODIAC_PRODUCTION_REJECTED: '+str(exc),file=sys.stderr); return 2

if __name__=='__main__': raise SystemExit(main())
