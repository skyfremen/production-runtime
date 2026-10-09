"""Zodiac transport: immutable intake manifest and verified result-only writeback."""
import argparse
import json
from pathlib import Path
import re
import subprocess
from .production import load_execution
from .lifecycle import encoded, load, immutable, SHA


def record_result(root,result):
    root=Path(root).resolve()
    cid=result.get('content_id')
    if not isinstance(cid,str) or not re.fullmatch(r'za-[a-z0-9-]{8,64}',cid): raise ValueError('RESULT_CONTENT_ID')
    if (root/'content/abandonments'/f'{cid}.json').exists(): raise ValueError('EXECUTION_ABANDONED')
    if result.get('result_version')!=2:
        from .lifecycle import ingest_result
        # Legacy provenance validator rebuilds derived context in memory/on disk;
        # restore its exact previous bytes so the runtime owns only results.
        context=root/'content/context.json'; previous=context.read_bytes() if context.exists() else None
        try: ingest_result(root,result)
        finally:
            if previous is None: context.unlink(missing_ok=True)
            else: context.write_bytes(previous)
        return root/f"content/results/{result['execution_id']}.json"
    keys={'result_version','execution_id','content_id','request_id','request_blob_sha','item_blob_sha','runtime_sha','status','visibility','verified',
        'youtube_video_id','source_sha','qc_passed','artifact_name','video_sha256','publish_at'}
    if set(result)!=keys: raise ValueError('RESULT_SCHEMA')
    request,item,execution=load_execution(root,result['execution_id'],source_sha=result['source_sha'],runtime_sha=result['runtime_sha'],repository='skyfremen/zodiac-workflow')
    for key in ('execution_id','content_id','request_id','request_blob_sha','item_blob_sha','runtime_sha'):
        if result.get(key)!=execution.get(key): raise ValueError('RESULT_PROVENANCE: '+key)
    if not (result['status']=='rendered' and result['visibility']=='private' and result['verified'] is True and result['qc_passed'] is True and
        result['youtube_video_id'] is None and result['artifact_name']==execution['execution_id'] and result['publish_at']==item['publish_at'] and
        re.fullmatch(r'[0-9a-f]{64}',str(result['video_sha256']))): raise ValueError('RESULT_INVALID')
    target=root/f"content/results/{result['content_id']}.json"; immutable(target,result); return target


def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument('action',choices=('fetch','result'))
    p.add_argument('--root',required=True); p.add_argument('--execution-id'); p.add_argument('--source-sha'); p.add_argument('--runtime-sha')
    p.add_argument('--output'); p.add_argument('--result'); p.add_argument('--repository',default='skyfremen/zodiac-workflow')
    a=p.parse_args(argv)
    if a.action=='result': record_result(a.root,load(a.result)); return 0
    root=Path(a.root).resolve()
    head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    if head!=a.source_sha: raise ValueError('PRIVATE_CHECKOUT_REVISION_MISMATCH')
    load_execution(root,a.execution_id,source_sha=a.source_sha,runtime_sha=a.runtime_sha,repository=a.repository)
    manifest={'root':str(root),'execution_id':a.execution_id,'source_sha':a.source_sha,'runtime_sha':a.runtime_sha,'repository':a.repository}
    Path(a.output).write_bytes(encoded(manifest)); return 0

if __name__=='__main__': raise SystemExit(main())
