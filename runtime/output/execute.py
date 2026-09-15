"""V2 prepare/upload stages for scheduled YouTube publication."""
import argparse, json, os
from pathlib import Path
from base.contract import OUTPUT_DIR, atomic_write_json, load_json
from output.state import GitHubState, RecoveryBlocked, identity_for
from output.transfer import make_client, prepare_upload, upload_new
def emit(name,value):
    out=os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out,"a",encoding="utf-8") as f: f.write(f"{name}={value}\n")
def prepare(path):
    request=load_json(path); identity=identity_for(path,request); state=GitHubState(); decision=prepare_upload(state,path,request,identity,make_client()); atomic_write_json(OUTPUT_DIR/"prepare.json",{"identity":identity,**decision}); emit("upload_required","true" if decision["upload_required"] else "false"); print("Upload prepare PASS")
def upload(path):
    request=load_json(path); identity=identity_for(path,request); state=GitHubState(); video=OUTPUT_DIR/"short.mp4"
    if not video.exists() or video.stat().st_size<10000: raise RecoveryBlocked("Rendered video is missing or suspiciously small")
    evidence=upload_new(state,path,request,identity,video,make_client()); atomic_write_json(OUTPUT_DIR/"upload_result.json",{"content_id":identity["content_id"],"youtube_video_id":evidence["youtube_video_id"],"upload_evidence":evidence}); print(f"Upload PASS {evidence['youtube_video_id']}")
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--stage",required=True,choices=("prepare","upload")); ap.add_argument("--request",required=True); a=ap.parse_args()
    try: prepare(a.request) if a.stage=="prepare" else upload(a.request)
    except RecoveryBlocked as e: raise SystemExit(str(e)) from None
if __name__=="__main__": main()
