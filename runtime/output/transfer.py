"""Scheduled-private YouTube transfer with append-only duplicate-upload fencing."""
import os, re
from base.contract import EXPECTED_YOUTUBE_CHANNEL_ID, marker_tag, request_content_id
from output.state import RecoveryBlocked, check_identity, evidence_path, now, workflow_identity

YOUTUBE_SCOPES=["https://www.googleapis.com/auth/youtube.upload","https://www.googleapis.com/auth/youtube.readonly"]
VIDEO_ID=re.compile(r"[A-Za-z0-9_-]{11}"); RECOVERY_SCAN_LIMIT=250

def _credential(name):
    value=os.environ.get(name)
    if not value: raise KeyError(name)
    return value

def make_client():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds=Credentials(token=None,refresh_token=_credential("RUNTIME_AUTH_C"),token_uri="https://oauth2.googleapis.com/token",client_id=_credential("RUNTIME_AUTH_A"),client_secret=_credential("RUNTIME_AUTH_B"),scopes=YOUTUBE_SCOPES)
    return build("youtube","v3",credentials=creds,cache_discovery=False)

def authenticated_channel(youtube):
    items=youtube.channels().list(part="id,snippet,contentDetails",mine=True).execute().get("items",[])
    if len(items)!=1 or items[0].get("id")!=EXPECTED_YOUTUBE_CHANNEL_ID: raise RecoveryBlocked("Credentials do not resolve to the pinned production channel")
    return items[0]

def _append_tag(tags,seen,value):
    clean=str(value or "").strip().lstrip("#")
    if clean and clean.lower() not in seen: tags.append(clean); seen.add(clean.lower())

def build_upload_body(request):
    if request.get("visibility")!="private": raise ValueError("V2 scheduled upload must be private")
    publication=request.get("publication") or {}
    if publication.get("mode")!="scheduled" or not publication.get("publish_at"): raise ValueError("V2 scheduled publication is required")
    youtube=request["youtube"]; cid=request_content_id(request); marker=marker_tag(cid); description=str(youtube["description"]).strip()
    existing={v.lower() for v in re.findall(r"(?<!\w)#[A-Za-z0-9_]+",description)}; extras=[]
    for h in youtube["hashtags"]:
        h=str(h).strip()
        if h and h.lower() not in existing: extras.append(h); existing.add(h.lower())
    if extras: description+="\n\n"+" ".join(extras)
    if len(description.encode())>5000: raise ValueError("Description exceeds 5000-byte remote limit")
    tags=[marker]; seen={marker.lower()}
    for t in youtube["tags"]: _append_tag(tags,seen,t)
    for h in youtube["hashtags"]: _append_tag(tags,seen,h)
    cost=sum(len(t)+(2 if " " in t else 0) for t in tags)+max(0,len(tags)-1)
    if cost>500: raise ValueError("Tags exceed remote combined limit")
    return {"snippet":{"title":str(youtube["title"]),"description":description,"tags":tags,"categoryId":str(youtube["category_id"])},"status":{"privacyStatus":"private","publishAt":str(publication["publish_at"]),"selfDeclaredMadeForKids":False}}

def _recent_video_ids(youtube,channel):
    uploads=((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    if not uploads: raise RecoveryBlocked("Authenticated channel uploads playlist is unavailable")
    ids=[]; token=None
    while len(ids)<RECOVERY_SCAN_LIMIT:
        resp=youtube.playlistItems().list(part="contentDetails",playlistId=uploads,maxResults=50,pageToken=token).execute(); ids += [x.get("contentDetails",{}).get("videoId") for x in resp.get("items",[]) if x.get("contentDetails",{}).get("videoId")]; token=resp.get("nextPageToken")
        if not token: break
    return ids[:RECOVERY_SCAN_LIMIT]

def reconcile_intent(youtube,intent,identity,channel):
    check_identity(intent,identity); marker=marker_tag(identity["content_id"]); matches=[]; ids=_recent_video_ids(youtube,channel)
    for start in range(0,len(ids),50):
        items=youtube.videos().list(part="snippet,status",id=",".join(ids[start:start+50]),maxResults=50).execute().get("items",[])
        for item in items:
            snippet=item.get("snippet") or {}
            if snippet.get("channelId")==channel["id"] and marker in (snippet.get("tags") or []): matches.append(item)
    if len(matches)>1: raise RecoveryBlocked("More than one remote video carries this immutable content marker")
    return matches[0] if matches else None

def upload_record(identity,intent,video_id,channel_id,association):
    if not VIDEO_ID.fullmatch(str(video_id or "")): raise RecoveryBlocked("Upload record requires valid video ID")
    return {"evidence_version":1,"record_type":"upload",**identity,"youtube_video_id":str(video_id),"expected_channel_id":channel_id,"upload_body":intent["upload_body"],"association":association,"recorded_at":now(),"workflow":workflow_identity()}

def prepare_upload(state,request_path,request,identity,youtube):
    channel=authenticated_channel(youtube); stored_upload=state.load(evidence_path(identity["content_id"],"upload"))
    if stored_upload: check_identity(stored_upload.data,identity); return {"upload_required":False,"upload_evidence":stored_upload.data}
    stored_intent=state.load(evidence_path(identity["content_id"],"intent"))
    if stored_intent:
        check_identity(stored_intent.data,identity); found=reconcile_intent(youtube,stored_intent.data,identity,channel)
        if found:
            rec=upload_record(identity,stored_intent.data,found["id"],channel["id"],"marker_reconciliation"); stored=state.create(evidence_path(identity["content_id"],"upload"),rec); return {"upload_required":False,"upload_evidence":stored.data}
        raise RecoveryBlocked("Upload intent exists but remote outcome is ambiguous; duplicate upload forbidden")
    return {"upload_required":True}

def upload_new(state,request_path,request,identity,video_path,youtube):
    channel=authenticated_channel(youtube); stored_upload=state.load(evidence_path(identity["content_id"],"upload"))
    if stored_upload: check_identity(stored_upload.data,identity); return stored_upload.data
    stored_intent=state.load(evidence_path(identity["content_id"],"intent"))
    if stored_intent:
        check_identity(stored_intent.data,identity); found=reconcile_intent(youtube,stored_intent.data,identity,channel)
        if found:
            rec=upload_record(identity,stored_intent.data,found["id"],channel["id"],"marker_reconciliation"); return state.create(evidence_path(identity["content_id"],"upload"),rec).data
        raise RecoveryBlocked("Upload intent exists but remote outcome is ambiguous; duplicate upload forbidden")
    body=build_upload_body(request); intent={"evidence_version":1,"record_type":"intent",**identity,"expected_channel_id":channel["id"],"upload_body":body,"created_at":now(),"workflow":workflow_identity()}; stored_intent=state.create(evidence_path(identity["content_id"],"intent"),intent)
    if not stored_intent.created: raise RecoveryBlocked("Upload intent was not freshly created at the insert boundary; duplicate upload forbidden")
    found=reconcile_intent(youtube,stored_intent.data,identity,channel)
    if found:
        rec=upload_record(identity,stored_intent.data,found["id"],channel["id"],"pre_insert_marker_reconciliation"); return state.create(evidence_path(identity["content_id"],"upload"),rec).data
    from googleapiclient.http import MediaFileUpload
    media=MediaFileUpload(str(video_path),mimetype="video/mp4",chunksize=-1,resumable=True); request_call=youtube.videos().insert(part="snippet,status",body=stored_intent.data["upload_body"],media_body=media,notifySubscribers=False); response=None
    while response is None: _status,response=request_call.next_chunk(num_retries=0)
    video_id=str((response or {}).get("id") or "")
    if not VIDEO_ID.fullmatch(video_id): raise RecoveryBlocked("YouTube insert did not return a valid video ID; retry must reconcile, not reinsert")
    rec=upload_record(identity,stored_intent.data,video_id,channel["id"],"videos.insert"); return state.create(evidence_path(identity["content_id"],"upload"),rec).data
