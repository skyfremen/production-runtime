"""Fetch one exact V1 execution from private state."""
import argparse, base64, json, re
from pathlib import Path
from urllib.parse import quote
from output.state import GitHubState, RecoveryBlocked, blob_sha

EID=re.compile(r"ex-[0-9a-f]{24}"); DID=re.compile(r"dp-[0-9a-f]{20}"); CID=re.compile(r"wd-[0-9a-f]{24}")
SHA40=re.compile(r"[0-9a-f]{40}"); SHA64=re.compile(r"[0-9a-f]{64}")
EXEC_PREFIX="content/executions/"; REQUEST_PREFIX="content/requests/"; REGISTRY_PATH="data/backgrounds.json"; REGISTRY=REGISTRY_PATH

def exact(state,path,ref):
    if not SHA40.fullmatch(ref): raise RecoveryBlocked("Exact 40-hex private source SHA required")
    result=state.api(f"contents/{quote(path,safe='/')}?ref={ref}")
    if result.get("type")!="file" or result.get("encoding")!="base64": raise RecoveryBlocked(f"Private state is not a file: {path}")
    raw=base64.b64decode("".join(str(result.get("content","")).split()),validate=True)
    if blob_sha(raw)!=result.get("sha"): raise RecoveryBlocked(f"Private blob integrity mismatch: {path}")
    return raw,result["sha"]

def fetch(execution_id,source_sha,contract_hash,dispatch_id,output):
    if not EID.fullmatch(execution_id) or not SHA40.fullmatch(source_sha) or not SHA64.fullmatch(contract_hash) or not DID.fullmatch(dispatch_id):
        raise RecoveryBlocked("Invalid opaque execution identity")
    state=GitHubState(); raw,_=exact(state,f"content/executions/{execution_id}.json",source_sha)
    execution=json.loads(raw)
    expected={"execution_version":1,"execution_id":execution_id,"contract_hash":contract_hash,"dispatch_id":dispatch_id,"state":"prepared"}
    if any(execution.get(k)!=v for k,v in expected.items()): raise RecoveryBlocked("Execution fence does not match opaque dispatch")
    cid=str(execution.get("content_id") or ""); request_path=str(execution.get("request_path") or "")
    request_source=str(execution.get("request_source_sha") or ""); request_blob=str(execution.get("request_blob_sha") or "")
    if not CID.fullmatch(cid) or request_path!=f"content/requests/{cid}.json" or not SHA40.fullmatch(request_source) or not SHA40.fullmatch(request_blob):
        raise RecoveryBlocked("Execution exact request identity is invalid")
    request_raw,observed=exact(state,request_path,source_sha)
    if observed!=request_blob: raise RecoveryBlocked("Request blob differs from immutable execution fence")
    original_raw,original_blob=exact(state,request_path,request_source)
    if original_blob!=request_blob or original_raw!=request_raw: raise RecoveryBlocked("Request creation revision differs from execution fence")
    registry_raw,_=exact(state,REGISTRY,source_sha)
    request_local=Path(f"runtime/content/requests/{cid}.json"); registry_local=Path("runtime/data/backgrounds.json")
    request_local.parent.mkdir(parents=True,exist_ok=True); registry_local.parent.mkdir(parents=True,exist_ok=True)
    request_local.write_bytes(request_raw); registry_local.write_bytes(registry_raw)
    manifest={"manifest_version":1,"execution_id":execution_id,"dispatch_id":dispatch_id,"source_sha":source_sha,"contract_hash":contract_hash,
              "requests":[request_local.as_posix()],"registry":registry_local.as_posix(),
              "request_sources":{request_local.as_posix():{"source_commit_sha":request_source,"request_blob_sha":request_blob}}}
    Path(output).write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(f"Load PASS execution={execution_id} content={cid}")

def main():
    ap=argparse.ArgumentParser(); p=ap.add_subparsers(dest="cmd",required=True).add_parser("fetch")
    for n in ("execution-id","source-sha","contract-hash","dispatch-id"): p.add_argument("--"+n,required=True)
    p.add_argument("--output",default="/tmp/runtime-execution.json"); a=ap.parse_args()
    try: fetch(a.execution_id,a.source_sha,a.contract_hash,a.dispatch_id,a.output)
    except (RecoveryBlocked,KeyError,ValueError,json.JSONDecodeError) as e: raise SystemExit(str(e)) from None
if __name__=="__main__": main()
