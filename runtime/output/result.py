"""Write one immutable compact V2 scheduled production result back to private state."""
import argparse, os, re
from base.contract import OUTPUT_DIR, load_json
from output.state import GitHubState, RecoveryBlocked, identity_for, result_path
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--request",required=True); a=ap.parse_args(); request=load_json(a.request); identity=identity_for(a.request,request); execution_id=os.environ.get("EXECUTION_ID","")
    if not re.fullmatch(r"ex-[0-9a-f]{24}",execution_id): raise RecoveryBlocked("Exact execution_id is required")
    verification=load_json(OUTPUT_DIR/"youtube-verification.json")
    if verification.get("passed") is not True or verification.get("privacy_status")!="private" or not verification.get("publish_at"): raise RecoveryBlocked("Verified scheduled-private evidence is required")
    payload={"result_version":2,"content_id":identity["content_id"],"execution_id":execution_id,"status":"scheduled","youtube_video_id":verification["youtube_video_id"],"visibility":"private","verified":True,"publish_at":verification["publish_at"]}
    stored=GitHubState().create(result_path(identity["content_id"]),payload)
    if stored.data!=payload: raise RecoveryBlocked("Durable result differs")
    print(f"Result PASS {identity['content_id']} -> {verification['youtube_video_id']} scheduled {verification['publish_at']}")
if __name__=="__main__":
    try: main()
    except RecoveryBlocked as e: raise SystemExit(str(e)) from None
