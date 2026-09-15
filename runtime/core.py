"""Single-video Wacky Dramas V2 production coordinator."""
import argparse, json, os, subprocess, sys
from pathlib import Path
from engine.pipeline import ProductionPipeline
from guard.preflight import run_preflight
from guard.schema import validate_request_data
from resources.validate import load_registry, validate_request_backgrounds

def run_cmd(cmd,env):
    p=subprocess.run(cmd,env=env,text=True)
    if p.returncode: raise RuntimeError(f"{Path(cmd[1]).name if len(cmd)>1 else cmd[0]} failed")
def run(manifest_path):
    manifest=json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    requests=manifest.get("requests")
    if manifest.get("manifest_version")!=1 or not isinstance(requests,list) or len(requests)!=1: raise RuntimeError("Execution manifest v1 must contain exactly one request")
    request=requests[0]; data=json.loads(Path(request).read_text(encoding="utf-8")); validate_request_data(data)
    validate_request_backgrounds(data,load_registry(manifest["registry"]))
    mapping=manifest.get("request_sources") or {}
    if request not in mapping: raise RuntimeError("Exact request source mapping is missing")
    env=dict(os.environ); env["REQUEST_SOURCE_MAP_JSON"]=json.dumps(mapping,separators=(",",":")); env["EXECUTION_ID"]=manifest["execution_id"]
    run_preflight()
    summary=ProductionPipeline(1,base_env=env).run([request])
    if int(summary.get("failed",0)): raise RuntimeError("Production failed before remote verification")
    identity=mapping[request]
    verify_env=dict(env); verify_env["SOURCE_COMMIT_SHA"]=identity["source_commit_sha"]; verify_env["SOURCE_REQUEST_BLOB_SHA"]=identity["request_blob_sha"]
    run_cmd(["python","runtime/output/verify.py","--request",request],verify_env)
    run_cmd(["python","runtime/output/result.py","--request",request],verify_env)
    Path("/tmp/runtime-public-summary.json").write_text(json.dumps({"success":1,"failed":0,"execution_id":manifest["execution_id"]},sort_keys=True)+"\n")
    print("V2 production PASS")
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--manifest",default="/tmp/runtime-execution.json"); a=ap.parse_args()
    try: run(a.manifest)
    except Exception as e:
        print(f"::error::E_EXEC_001 {e}",file=sys.stderr); raise SystemExit(1)
if __name__=="__main__": main()
