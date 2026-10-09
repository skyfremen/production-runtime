"""Isolated exact-revision Zodiac production, called only by its private workflow."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import sys
from .content import validate_submission as validate, Rejected
from .contract import REPOSITORY, SHA, EX2, load, encoded, ContractRejected

class ProductionRejected(ValueError): pass

def require(ok,reason):
    if not ok: raise ProductionRejected(reason)

def load_execution(root,execution_id,*,source_sha,runtime_sha,repository):
    require(repository==REPOSITORY,'ZODIAC_REPOSITORY_REQUIRED')
    require(isinstance(execution_id,str) and EX2.fullmatch(execution_id),'EXECUTION_ID')
    require(isinstance(source_sha,str) and SHA.fullmatch(source_sha),'SOURCE_SHA')
    require(isinstance(runtime_sha,str) and SHA.fullmatch(runtime_sha),'RUNTIME_SHA')
    root=Path(root).resolve()
    from .contract import intake, ContractRejected
    try: return intake(root,execution_id,source_sha=source_sha,runtime_sha=runtime_sha)
    except ContractRejected as exc: raise ProductionRejected(str(exc)) from None

def produce(root,execution_id,*,source_sha,runtime_sha,repository,output):
    request,item,execution=load_execution(root,execution_id,source_sha=source_sha,runtime_sha=runtime_sha,repository=repository)
    from .cards import generate
    validated=validate({'winner_count':1,'winners':[item['winner']]})
    with tempfile.TemporaryDirectory(prefix='zodiac-intake-') as folder:
        path=Path(folder)/'validated.json'; path.write_bytes(encoded(validated))
        generate(path,output,catalogue_root=root)
    manifest=load(Path(output)/'manifest.json'); video=manifest['videos'][0]
    result={**{k:execution[k] for k in ('execution_id','content_id','request_id','request_blob_sha','item_blob_sha')},'runtime_sha':runtime_sha,
        'result_version':2,'status':'rendered','visibility':'private','verified':True,'youtube_video_id':None,
        'source_sha':source_sha,'qc_passed':True,'artifact_name':execution_id,'video_sha256':video['sha256'],'publish_at':item['publish_at']}
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
        print(json.dumps({'execution_id':result['execution_id'],'state':result['status'],'qc_passed':True})); return 0
    except (ProductionRejected,ContractRejected,Rejected,ValueError,OSError,KeyError,TypeError,subprocess.CalledProcessError) as exc:
        print('ZODIAC_PRODUCTION_REJECTED: '+str(exc),file=sys.stderr); return 2

if __name__=='__main__': raise SystemExit(main())

