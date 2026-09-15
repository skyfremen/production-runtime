"""Small sequential V1 production pipeline for exactly one immutable request."""
import json, os, subprocess, tempfile, time
from pathlib import Path

class PipelineError(RuntimeError): pass

def _read_outputs(path):
    values={}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k,v=line.split("=",1); values[k]=v
    return values

class ProductionPipeline:
    def __init__(self,concurrency=1,*,base_env=None):
        if int(concurrency)!=1: raise PipelineError("V1 production supports exactly one request at a time")
        self.base_env=dict(base_env or os.environ)

    def _env(self,request):
        mapping=json.loads(self.base_env.get("REQUEST_SOURCE_MAP_JSON","{}")); identity=mapping.get(str(request))
        if not isinstance(identity,dict): raise PipelineError("Exact immutable request source mapping is missing")
        env=dict(self.base_env)
        env["SOURCE_COMMIT_SHA"]=str(identity.get("source_commit_sha") or "")
        env["SOURCE_REQUEST_BLOB_SHA"]=str(identity.get("request_blob_sha") or "")
        env["STORY_OUTPUT_DIR"]=f"runtime/output/{Path(request).stem}"
        env.setdefault("OMP_NUM_THREADS","2"); env.setdefault("MKL_NUM_THREADS","2")
        env.setdefault("OPENBLAS_NUM_THREADS","2"); env.setdefault("NUMEXPR_NUM_THREADS","2")
        env.setdefault("TORCH_NUM_THREADS","2"); env.setdefault("TORCH_INTEROP_THREADS","1")
        env.setdefault("FFMPEG_THREADS","2")
        return env

    @staticmethod
    def _call(command,env):
        timeout=int(env.get("RUNTIME_CHILD_TIMEOUT_SECONDS","1200"))
        if not 60<=timeout<=3600: raise PipelineError("RUNTIME_CHILD_TIMEOUT_SECONDS must be 60-3600")
        result=subprocess.run(command,env=env,text=True,timeout=timeout)
        if result.returncode: raise PipelineError(f"{Path(command[1]).name if len(command)>1 else command[0]} exited {result.returncode}")

    def run(self,requests):
        if not isinstance(requests,(list,tuple)) or len(requests)!=1: raise PipelineError("V1 pipeline requires exactly one request")
        request=str(requests[0]); env=self._env(request); started=time.monotonic()
        output=Path(env["STORY_OUTPUT_DIR"]); output.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="v1-prepare-",delete=False) as tmp: status_path=Path(tmp.name)
        try:
            prepare_env=dict(env); prepare_env["GITHUB_OUTPUT"]=str(status_path)
            self._call(["python","runtime/output/execute.py","--stage","prepare","--request",request],prepare_env)
            status=_read_outputs(status_path)
        finally: status_path.unlink(missing_ok=True)
        upload_required=status.get("upload_required")=="true"
        if upload_required:
            self._call(["python","runtime/resources/resolve.py","--request",request],env)
            self._call(["python","runtime/transform/process.py","--request",request],env)
            self._call(["python","runtime/transform/verify.py","--request",request],env)
            self._call(["python","runtime/output/execute.py","--stage","upload","--request",request],env)
        summary={"request_count":1,"upload_required":upload_required,"failed":0,"production_phase_seconds":round(time.monotonic()-started,6)}
        print(json.dumps(summary,sort_keys=True)); return summary
