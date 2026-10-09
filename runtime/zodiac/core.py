"""Dedicated Zodiac production entrypoint, matching Drama's manifest pattern."""
import argparse
import json
import subprocess
from .production import produce
from .contract import load

def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument('--manifest',required=True); parser.add_argument('--output',required=True)
    args=parser.parse_args(argv); manifest=load(args.manifest)
    if set(manifest)!={'root','execution_id','source_sha','runtime_sha','repository'}: raise ValueError('MANIFEST_SCHEMA')
    head=subprocess.check_output(['git','-C',manifest['root'],'rev-parse','HEAD'],text=True).strip()
    if head!=manifest['source_sha']: raise ValueError('PRIVATE_CHECKOUT_REVISION_MISMATCH')
    result=produce(manifest['root'],manifest['execution_id'],source_sha=manifest['source_sha'],runtime_sha=manifest['runtime_sha'],
        repository=manifest['repository'],output=args.output)
    print(json.dumps({'execution_id':result['execution_id'],'status':result.get('status',result.get('state')),'qc_passed':True}))
    return 0
if __name__=='__main__': raise SystemExit(main())

