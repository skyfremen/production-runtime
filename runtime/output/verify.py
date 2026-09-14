"""Exact remote verification for V1 immediate-public uploads."""
import argparse, time
from datetime import datetime
from pathlib import Path
from base.contract import OUTPUT_DIR, atomic_write_json, load_json, marker_tag
from output.state import GitHubState, RecoveryBlocked, check_identity, evidence_path, identity_for, now
from output.transfer import authenticated_channel, make_client
RETRY_DELAYS=(0,2,4,8,8,4,4,10,10,10)
def _instant(raw):
    try: return datetime.fromisoformat(str(raw).replace("Z","+00:00"))
    except ValueError: return None
def verify_video(youtube,request,identity,evidence,sleep=time.sleep):
    check_identity(evidence,identity)
    if evidence.get("record_type")!="upload" or not evidence.get("youtube_video_id"): raise RecoveryBlocked("Durable upload record required")
    channel=authenticated_channel(youtube)
    if channel["id"]!=evidence.get("expected_channel_id"): raise RecoveryBlocked("Upload evidence belongs to another channel")
    body=evidence.get("upload_body") or {}; expected=body.get("snippet") or {}; status_intent=body.get("status") or {}
    if status_intent.get("privacyStatus")!="public" or "publishAt" in status_intent: raise RecoveryBlocked("Durable intent is not immediate-public")
    marker=marker_tag(identity["content_id"]); video_id=evidence["youtube_video_id"]; observations=[]
    for attempt,delay in enumerate(RETRY_DELAYS,1):
        if delay: sleep(delay)
        items=youtube.videos().list(part="snippet,status,contentDetails",id=video_id).execute().get("items",[])
        if not items: observations.append({"attempt":attempt,"state":"not_visible"}); continue
        if len(items)!=1 or items[0].get("id")!=video_id: raise RecoveryBlocked("Remote service returned a different video ID")
        item=items[0]; snippet=item.get("snippet") or {}; status=item.get("status") or {}
        if snippet.get("channelId")!=channel["id"]: raise RecoveryBlocked("Video belongs to another channel")
        if status.get("uploadStatus") in {"failed","rejected","deleted"} or status.get("failureReason") or status.get("rejectionReason"):
            raise RecoveryBlocked("Remote service rejected or failed the upload")
        for field in ("title","description","categoryId"):
            if snippet.get(field)!=expected.get(field): raise RecoveryBlocked(f"Remote {field} differs from durable upload intent")
        if status.get("uploadStatus")!="processed": observations.append({"attempt":attempt,"state":"processing"}); continue
        if marker not in (snippet.get("tags") or []): observations.append({"attempt":attempt,"state":"marker_pending"}); continue
        if status.get("privacyStatus")!="public": raise RecoveryBlocked("Video is not PUBLIC after processing")
        if "publishAt" in status: raise RecoveryBlocked("Immediate-public V1 video unexpectedly exposes publishAt")
        published=snippet.get("publishedAt")
        if _instant(published) is None: raise RecoveryBlocked("Public video has no valid publishedAt")
        return {"passed":True,"state":"verified_public","verified_at":now(),**identity,"youtube_video_id":video_id,
                "channel_id":channel["id"],"privacy_status":"public","published_at":published,
                "upload_status":"processed","association_method":"immutable_upload_record+remote_marker",
                "observed_marker_tags":[marker],"attempts":attempt,"prior_observations":observations}
    raise RecoveryBlocked("Remote verification did not reach processed PUBLIC state within bounded attempts")
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--request",required=True); a=ap.parse_args()
    request=load_json(a.request); identity=identity_for(a.request,request); state=GitHubState()
    stored=state.load(evidence_path(identity["content_id"],"upload"))
    if not stored: raise RecoveryBlocked("Durable upload evidence is missing")
    verification=verify_video(make_client(),request,identity,stored.data)
    upload_path=OUTPUT_DIR/"upload_result.json"
    payload=load_json(upload_path) if upload_path.exists() else {"content_id":identity["content_id"],"youtube_video_id":verification["youtube_video_id"],"upload_evidence":stored.data}
    payload["verification"]=verification; payload["visibility"]="public"; payload["verified"]=True
    atomic_write_json(upload_path,payload); atomic_write_json(OUTPUT_DIR/"youtube-verification.json",verification)
    print(f"Remote verification PASS {verification['youtube_video_id']} PUBLIC")
if __name__=="__main__":
    try: main()
    except RecoveryBlocked as e: raise SystemExit(str(e)) from None
