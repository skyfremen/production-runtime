"""Exact immutable private-state transport for Wacky Dramas V2 batch requests."""
import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from base.compat import validate_contract_hash
from base.contract import CONTENT_ID_RE, EXECUTION_ID_RE

SHA40 = re.compile(r"^[0-9a-f]{40}$")
REQUEST_ID_RE = re.compile(r"^rq-[0-9a-f]{24}$")
DISPATCH_ID_RE = re.compile(r"^dp-[0-9a-f]{20}$")
PRIVATE_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

class TransportError(RuntimeError): pass

def git_blob_sha(raw): return hashlib.sha1(f"blob {len(raw)}\0".encode()+raw).hexdigest()
def encoded_json(data): return (json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()

class PrivateState:
    def __init__(self):
        self.repo=str(os.environ.get("PRIVATE_STATE_REPOSITORY") or ""); self.token=str(os.environ.get("PRIVATE_STATE_TOKEN") or "")
        if not PRIVATE_REPO_RE.fullmatch(self.repo) or not self.token: raise TransportError("Invalid private-state configuration")
    def read(self,path,ref):
        if not SHA40.fullmatch(str(ref or "")): raise TransportError("Exact 40-hex private source revision is required")
        url=f"https://api.github.com/repos/{self.repo}/contents/{quote(path,safe='/')}?ref={ref}"
        request=Request(url,headers={"Authorization":f"Bearer {self.token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"})
        try:
            with urlopen(request,timeout=45) as response: result=json.load(response)
        except HTTPError as exc: raise TransportError(f"Private state missing/unreadable: {path} (HTTP {exc.code})") from None
        except (URLError,TimeoutError): raise TransportError(f"Private state network failure: {path}") from None
        if result.get("type")!="file" or result.get("encoding")!="base64": raise TransportError(f"Private state is not a file: {path}")
        try: raw=base64.b64decode("".join(str(result.get("content") or "").split()),validate=True)
        except ValueError: raise TransportError(f"Invalid base64 private state: {path}") from None
        if result.get("sha")!=git_blob_sha(raw): raise TransportError(f"Private state blob integrity mismatch: {path}")
        return raw

def _json(raw,label):
    try: value=json.loads(raw)
    except (UnicodeDecodeError,json.JSONDecodeError): raise TransportError(f"Invalid JSON in {label}") from None
    if not isinstance(value,dict): raise TransportError(f"{label} must be a JSON object")
    return value

def fetch_execution(execution_id,source_sha,output):
    if not EXECUTION_ID_RE.fullmatch(execution_id): raise TransportError("Invalid execution_id")
    if not SHA40.fullmatch(source_sha): raise TransportError("Invalid source_sha")
    state=PrivateState(); execution_path=f"content/executions/{execution_id}.json"
    execution_raw=state.read(execution_path,source_sha); execution=_json(execution_raw,execution_path)
    if execution.get("execution_version")!=2: raise TransportError("Execution fence has unsupported execution_version")
    if execution.get("execution_id")!=execution_id or execution.get("state")!="prepared": raise TransportError("Execution fence does not match immutable dispatch identity")
    try: contract_hash=validate_contract_hash(execution.get("contract_hash"))
    except ValueError as exc: raise TransportError(str(exc)) from None
    dispatch_id=str(execution.get("dispatch_id") or "")
    if not DISPATCH_ID_RE.fullmatch(dispatch_id): raise TransportError("Execution fence has invalid dispatch_id")
    content_id=str(execution.get("content_id") or "")
    if not CONTENT_ID_RE.fullmatch(content_id): raise TransportError("Execution fence has invalid content_id")
    request_id=str(execution.get("request_id") or "")
    if not REQUEST_ID_RE.fullmatch(request_id): raise TransportError("Execution fence has invalid request_id")
    request_path=str(execution.get("request_path") or "")
    if request_path!=f"content/requests/{request_id}.json": raise TransportError("Execution fence has invalid request_path")
    request_source_sha=str(execution.get("request_source_sha") or ""); request_blob_sha=str(execution.get("request_blob_sha") or ""); item_blob_sha=str(execution.get("item_blob_sha") or "")
    if not SHA40.fullmatch(request_source_sha) or not SHA40.fullmatch(request_blob_sha) or not SHA40.fullmatch(item_blob_sha): raise TransportError("Execution fence lacks exact immutable batch/item identity")
    batch_raw=state.read(request_path,request_source_sha)
    if git_blob_sha(batch_raw)!=request_blob_sha: raise TransportError("Immutable batch request blob differs from execution fence")
    batch=_json(batch_raw,request_path)
    if batch.get("request_version")!=2 or batch.get("request_id")!=request_id: raise TransportError("Immutable batch request identity differs from execution fence")
    items=batch.get("items")
    if not isinstance(items,list): raise TransportError("Immutable batch request has invalid items")
    matches=[item for item in items if isinstance(item,dict) and item.get("content_id")==content_id]
    if len(matches)!=1: raise TransportError("Immutable batch request does not contain exactly one execution item")
    item=matches[0]; item_raw=encoded_json(item)
    if git_blob_sha(item_raw)!=item_blob_sha: raise TransportError("Immutable batch item differs from execution fence")
    registry_path="data/backgrounds.json"; registry_raw=state.read(registry_path,source_sha); registry=_json(registry_raw,registry_path)
    if not isinstance(registry.get("assets"),list): raise TransportError("Background registry must contain assets[]")
    local_request=Path(f"runtime/content/requests/{content_id}.json"); local_registry=Path("runtime/data/backgrounds.json")
    local_request.parent.mkdir(parents=True,exist_ok=True); local_registry.parent.mkdir(parents=True,exist_ok=True)
    local_request.write_bytes(item_raw); local_registry.write_bytes(registry_raw)
    manifest={"manifest_version":1,"execution_id":execution_id,"content_id":content_id,"requests":[local_request.as_posix()],"registry":local_registry.as_posix(),
              "request_sources":{local_request.as_posix():{"source_commit_sha":request_source_sha,"request_blob_sha":item_blob_sha}},
              "private_execution_source_sha":source_sha,"contract_hash":contract_hash,"dispatch_id":dispatch_id}
    Path(output).write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(f"Fetch PASS execution={execution_id} content={content_id} batch={request_id}"); return manifest

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("action",choices=("fetch",)); parser.add_argument("--execution-id",required=True); parser.add_argument("--source-sha",required=True); parser.add_argument("--output",default="/tmp/runtime-execution.json"); args=parser.parse_args()
    try: fetch_execution(args.execution_id,args.source_sha,args.output)
    except (TransportError,ValueError) as exc: raise SystemExit(str(exc)) from None

if __name__=="__main__": main()
