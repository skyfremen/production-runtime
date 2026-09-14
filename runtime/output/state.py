"""Append-only V1 upload evidence. An intent is an irreversible duplicate-upload fence."""
import base64, hashlib, json, os, re, time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from base.contract import ensure_request_path_matches, validate_content_id

class RecoveryBlocked(RuntimeError): pass
def now(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def blob_sha(raw): return hashlib.sha1(f"blob {len(raw)}\0".encode()+raw).hexdigest()
def encoded_json(data): return (json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True)+"\n").encode()
def evidence_path(content_id,kind):
    validate_content_id(content_id)
    if kind not in {"intent","upload"}: raise ValueError("Unknown evidence kind")
    return f"content/executions/evidence/{content_id}/{kind}.json"
def result_path(content_id): validate_content_id(content_id); return f"content/results/{content_id}.json"
@dataclass
class Stored:
    data:dict; sha:str; created:bool=False; commit:str|None=None
class GitHubState:
    def __init__(self):
        self.repo=os.environ["PRIVATE_STATE_REPOSITORY"]; self.token=os.environ["PRIVATE_STATE_TOKEN"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",self.repo): raise RecoveryBlocked("Invalid private state repository")
    def api(self,path,method="GET",body=None):
        req=Request(f"https://api.github.com/repos/{self.repo}/{path}",data=encoded_json(body) if body is not None else None,method=method,
            headers={"Authorization":f"Bearer {self.token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","Content-Type":"application/json"})
        with urlopen(req,timeout=30) as res: return json.load(res)
    @staticmethod
    def allowed(path):
        m=re.fullmatch(r"content/(?:executions/evidence/([^/]+)/(?:intent|upload)|results/([^/]+))\.json",path)
        if not m: raise RecoveryBlocked("State writes are limited to V1 execution evidence and results")
        validate_content_id(next(x for x in m.groups() if x))
    def load(self,path):
        self.allowed(path)
        try: result=self.api(f"contents/{path}?ref=main")
        except HTTPError as e:
            if e.code==404: return None
            raise RecoveryBlocked(f"Cannot read durable state (HTTP {e.code}); upload forbidden") from None
        raw=base64.b64decode("".join(str(result["content"]).split()),validate=True)
        if blob_sha(raw)!=result["sha"]: raise RecoveryBlocked("Durable evidence blob integrity mismatch")
        return Stored(json.loads(raw),result["sha"])
    def create(self,path,data):
        self.allowed(path); prior=self.load(path)
        if prior:
            if prior.data!=data: raise RecoveryBlocked(f"Immutable state already exists with different content: {path}")
            return prior
        raw=encoded_json(data); result=None; last=None
        for attempt in range(4):
            try:
                result=self.api(f"contents/{path}",method="PUT",body={"branch":"main","message":f"[runtime] record {Path(path).stem} for {data.get('content_id','state')}",
                    "content":base64.b64encode(raw).decode()}); break
            except HTTPError as e:
                last=e
                if e.code not in {409,422}: break
                prior=self.load(path)
                if prior:
                    if prior.data!=data: raise RecoveryBlocked(f"Immutable state already exists with different content: {path}")
                    return prior
                if attempt<3: time.sleep(.25*(2**attempt))
            except Exception as e: last=e; break
        if result is None: raise RecoveryBlocked(f"Durable write not acknowledged ({type(last).__name__}); retry/recovery only") from None
        if result["content"]["sha"]!=blob_sha(raw): raise RecoveryBlocked("Durable write returned unexpected evidence SHA")
        return Stored(data,result["content"]["sha"],True,result["commit"]["sha"])
def identity_for(path,data):
    path=Path(path); cid=ensure_request_path_matches(path,data); expected=Path(f"runtime/content/requests/{cid}.json")
    if path.resolve()!=expected.resolve(): raise RecoveryBlocked("Publishing requires canonical local request path")
    source=os.environ.get("SOURCE_COMMIT_SHA",""); expected_blob=os.environ.get("SOURCE_REQUEST_BLOB_SHA","")
    if not re.fullmatch(r"[0-9a-f]{40}",source) or not re.fullmatch(r"[0-9a-f]{40}",expected_blob):
        raise RecoveryBlocked("Exact immutable request source and blob are required")
    raw=path.read_bytes()
    if blob_sha(raw)!=expected_blob: raise RecoveryBlocked("Request differs from immutable source")
    return {"content_id":cid,"request_path":f"content/requests/{cid}.json","request_blob_sha":expected_blob,"source_commit_sha":source}
def check_identity(evidence,identity):
    for k,v in identity.items():
        if evidence.get(k)!=v: raise RecoveryBlocked(f"Evidence {k} does not match immutable request")
def workflow_identity():
    head=os.environ.get("RUNTIME_COMMIT_SHA","")
    if not re.fullmatch(r"[0-9a-f]{40}",head): raise RecoveryBlocked("Exact runtime commit is required")
    return {"name":os.environ.get("GITHUB_WORKFLOW",""),"run_id":os.environ.get("GITHUB_RUN_ID",""),
            "run_attempt":os.environ.get("GITHUB_RUN_ATTEMPT",""),"code_commit_sha":head}
