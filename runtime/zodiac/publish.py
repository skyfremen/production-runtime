"""Zodiac-only scheduled publishing with durable intent and upload records."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import time
from output.state import GitHubState, RecoveryBlocked, now
from output.verify import RETRY_DELAYS
from .contract import CHANNEL, REPOSITORY, encoded, load
from .production import load_execution

VIDEO_ID=re.compile(r'^[A-Za-z0-9_-]{11}$')
CONTENT_ID=re.compile(r'^za-[a-z0-9-]{8,64}$')
IDENTITY_FIELDS=('execution_id','content_id','request_id','request_path','request_source_sha','request_blob_sha','item_blob_sha')

def require(ok,message):
    if not ok: raise RecoveryBlocked('Zodiac publishing: '+message)

def evidence_path(content_id,kind):
    require(isinstance(content_id,str) and CONTENT_ID.fullmatch(content_id),'invalid content identity')
    require(kind in ('intent','upload'),'invalid evidence kind')
    return f'content/executions/evidence/{content_id}/{kind}.json'

class ZodiacState(GitHubState):
    def __init__(self):
        self.repo=REPOSITORY; self.token=os.environ.get('ZODIAC_STATE_TOKEN','')
        require(bool(self.token),'ZODIAC_STATE_TOKEN missing')
    @staticmethod
    def allowed(path):
        require(bool(re.fullmatch(r'content/executions/evidence/za-[a-z0-9-]{8,64}/(?:intent|upload)\.json',path)),
            'state writes are limited to Zodiac upload evidence')

def identity_for(execution): return {key:execution[key] for key in IDENTITY_FIELDS}

def marker(content_id):
    evidence_path(content_id,'intent')
    return 'za-id-'+sha256(content_id.encode()).hexdigest()[:20]

def check_evidence(evidence,identity,kind):
    require(evidence.get('evidence_version')==2 and evidence.get('record_type')==kind and
        all(evidence.get(key)==value for key,value in identity.items()),'evidence identity mismatch')

def make_client():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    values={name:os.environ.get(name,'').strip() for name in ('ZODIAC_CLIENT_ID','ZODIAC_CLIENT_SECRET','ZODIAC_REFRESH_TOKEN')}
    require(all(values.values()),'Zodiac OAuth credentials missing')
    credentials=Credentials(token=None,refresh_token=values['ZODIAC_REFRESH_TOKEN'],
        token_uri='https://oauth2.googleapis.com/token',client_id=values['ZODIAC_CLIENT_ID'],client_secret=values['ZODIAC_CLIENT_SECRET'],
        scopes=['https://www.googleapis.com/auth/youtube.upload','https://www.googleapis.com/auth/youtube.readonly'])
    return build('youtube','v3',credentials=credentials,cache_discovery=False)

def channel_for(youtube,request):
    items=youtube.channels().list(part='id,snippet,contentDetails',mine=True).execute().get('items',[])
    expected=request['publication']['channel_id']
    require(len(items)==1 and items[0].get('id')==expected and
        str((items[0].get('snippet') or {}).get('customUrl','')).casefold()==CHANNEL['handle'].casefold(),
        'credentials resolve to the wrong channel')
    return items[0]

def upload_body(item):
    require(item['publication']['mode']=='scheduled' and item['visibility']=='private','scheduled-private request required')
    metadata=item['youtube']; description=str(metadata['description']).strip()
    description+='\n\n'+' '.join(metadata['hashtags'])
    require(len(description.encode())<=5000,'description too long')
    tags=[marker(item['content_id']),'Wacky Astrology','Zodiac','Shorts']
    return {'snippet':{'title':metadata['title'],'description':description,'tags':tags,'categoryId':'24'},
        'status':{'privacyStatus':'private','publishAt':item['publication']['publish_at'],'selfDeclaredMadeForKids':False}}

def reconcile(youtube,channel,identity):
    uploads=((channel.get('contentDetails') or {}).get('relatedPlaylists') or {}).get('uploads')
    require(bool(uploads),'channel uploads playlist missing')
    ids=[]; page=None
    for _ in range(5):
        response=youtube.playlistItems().list(part='contentDetails',playlistId=uploads,maxResults=50,pageToken=page).execute()
        ids.extend(v['contentDetails']['videoId'] for v in response.get('items',[]) if (v.get('contentDetails') or {}).get('videoId'))
        page=response.get('nextPageToken')
        if not page: break
    matches=[]
    for start in range(0,min(len(ids),250),50):
        response=youtube.videos().list(part='snippet,status',id=','.join(ids[start:start+50]),maxResults=50).execute()
        matches.extend(v for v in response.get('items',[]) if (v.get('snippet') or {}).get('channelId')==channel['id'] and
            marker(identity['content_id']) in ((v.get('snippet') or {}).get('tags') or []))
    require(len(matches)<=1,'multiple remote videos match the content marker')
    return matches[0] if matches else None

def upload_record(intent,video_id,association):
    require(bool(VIDEO_ID.fullmatch(str(video_id or ''))),'upload did not return a valid video ID; reconcile on retry')
    return {**intent,'record_type':'upload','youtube_video_id':video_id,'association':association,'recorded_at':now()}

def recover(state,youtube,identity,request):
    channel=channel_for(youtube,request)
    stored=state.load(evidence_path(identity['content_id'],'upload'))
    if stored:
        check_evidence(stored.data,identity,'upload')
        require(stored.data.get('expected_channel_id')==channel['id'],'upload evidence belongs to another channel')
        return stored.data
    intent=state.load(evidence_path(identity['content_id'],'intent'))
    if not intent: return None
    check_evidence(intent.data,identity,'intent')
    require(intent.data.get('expected_channel_id')==channel['id'],'intent belongs to another channel')
    found=reconcile(youtube,channel,identity)
    require(found is not None,'upload intent exists but remote outcome is ambiguous; duplicate upload forbidden')
    record=upload_record(intent.data,found['id'],'marker_reconciliation')
    return state.create(evidence_path(identity['content_id'],'upload'),record).data

def media_upload(video):
    from googleapiclient.http import MediaFileUpload
    return MediaFileUpload(str(video),mimetype='video/mp4',chunksize=-1,resumable=True)

def upload(state,youtube,request,item,identity,video,result):
    require(all(result.get(k)==identity[k] for k in ('execution_id','content_id','request_id','request_blob_sha','item_blob_sha')) and
        result.get('publish_at')==item['publication']['publish_at'],'render result identity mismatch')
    require(result.get('qc_passed') is True and result.get('verified') is True and
        result.get('status')=='rendered' and result.get('video_sha256')==sha256(Path(video).read_bytes()).hexdigest(),
        'passing QC and the exact verified MP4 are required')
    existing=recover(state,youtube,identity,request)
    if existing: return existing
    at=datetime.fromisoformat(item['publication']['publish_at'].replace('Z','+00:00'))
    require(at>datetime.now(timezone.utc),'publication slot must be in the future; do not change immutable slots')
    body=upload_body(item)
    intent={'evidence_version':2,'record_type':'intent',**identity,'expected_channel_id':request['publication']['channel_id'],
        'upload_body':body,'render_result':result,'created_at':now()}
    stored=state.create(evidence_path(identity['content_id'],'intent'),intent)
    require(stored.created,'intent was not freshly created; duplicate upload forbidden')
    found=reconcile(youtube,channel_for(youtube,request),identity)
    if found: record=upload_record(stored.data,found['id'],'pre_insert_marker_reconciliation')
    else:
        call=youtube.videos().insert(part='snippet,status',body=stored.data['upload_body'],media_body=media_upload(video),notifySubscribers=False)
        response=None
        while response is None: _,response=call.next_chunk(num_retries=0)
        record=upload_record(stored.data,(response or {}).get('id'),'videos.insert')
    return state.create(evidence_path(identity['content_id'],'upload'),record).data

def verify(youtube,request,item,identity,evidence,result,*,sleep=time.sleep):
    check_evidence(evidence,identity,'upload'); channel=channel_for(youtube,request)
    require(evidence.get('expected_channel_id')==channel['id'],'evidence belongs to another channel')
    expected=upload_body(item)
    require(evidence.get('upload_body')==expected and evidence.get('render_result')==result,'evidence differs from the verified request/video')
    video_id=evidence['youtube_video_id']; require(bool(VIDEO_ID.fullmatch(str(video_id))),'invalid video identity')
    at=datetime.fromisoformat(item['publication']['publish_at'].replace('Z','+00:00'))
    last_observation='video not visible'
    for attempt,delay in enumerate(RETRY_DELAYS,1):
        if delay: sleep(delay)
        items=youtube.videos().list(part='snippet,status,processingDetails',id=video_id).execute().get('items',[])
        if not items: continue
        require(len(items)==1 and items[0].get('id')==video_id,'remote video identity mismatch')
        snippet=items[0].get('snippet') or {}; status=items[0].get('status') or {}
        require(snippet.get('channelId')==channel['id'],'remote video belongs to another channel')
        require(status.get('uploadStatus') not in ('failed','rejected','deleted') and
            not status.get('failureReason') and not status.get('rejectionReason'),'YouTube rejected the upload')
        require(all(snippet.get(k)==expected['snippet'][k] for k in ('title','description','categoryId')),'remote metadata mismatch')
        marker_present=marker(identity['content_id']) in (snippet.get('tags') or [])
        if status.get('uploadStatus')!='processed' or not marker_present:
            upload_status=status.get('uploadStatus')
            processing_status=(items[0].get('processingDetails') or {}).get('processingStatus')
            if upload_status not in ('deleted','failed','processed','rejected','uploaded'): upload_status='unknown'
            if processing_status not in ('processing','succeeded','failed','terminated'): processing_status='unknown'
            last_observation=f'upload_status={upload_status}; processing_status={processing_status}; marker_present={marker_present}'
            print(f'Zodiac verification {attempt}: video={video_id}; {last_observation}',flush=True)
            continue
        privacy=status.get('privacyStatus'); observed=status.get('publishAt')
        if privacy=='private':
            require(observed is not None and datetime.fromisoformat(observed.replace('Z','+00:00'))==at,'remote schedule mismatch')
            outcome='scheduled'
        elif privacy=='public' and datetime.now(timezone.utc)>=at:
            require(not observed or datetime.fromisoformat(observed.replace('Z','+00:00'))==at,'remote schedule mismatch')
            outcome='published'
        else: raise RecoveryBlocked('Zodiac publishing: unexpected visibility for the reserved slot')
        return {**result,'status':outcome,'visibility':privacy,'youtube_video_id':video_id,'verified':True}
    raise RecoveryBlocked('Zodiac publishing: remote verification pending; '+last_observation+'; rerun to verify the same upload')

def validate_recorded_result(root,request,item,execution,result):
    identity=identity_for(execution); records={}
    for kind in ('intent','upload'):
        path=Path(root)/evidence_path(identity['content_id'],kind)
        require(path.is_file(),'upload evidence missing: '+kind)
        record=load(path); check_evidence(record,identity,kind)
        require(record.get('expected_channel_id')==request['publication']['channel_id'] and
            record.get('upload_body')==upload_body(item),'upload evidence differs from scheduled request')
        rendered=record.get('render_result',{})
        require(set(rendered)==set(result) and all(rendered.get(k)==v for k,v in result.items() if k not in ('status','visibility','youtube_video_id')) and
            rendered.get('status')=='rendered' and rendered.get('visibility')=='private' and rendered.get('youtube_video_id') is None,
            'upload evidence differs from verified video')
        records[kind]=record
    require(records['upload'].get('youtube_video_id')==result['youtube_video_id'] and
        records['upload']['upload_body']==records['intent']['upload_body'] and
        records['upload']['render_result']==records['intent']['render_result'],'upload/intent evidence mismatch')


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=('prepare','upload'))
    parser.add_argument('--manifest',required=True); parser.add_argument('--output',required=True)
    args=parser.parse_args(argv); manifest=load(args.manifest)
    request,item,execution=load_execution(manifest['root'],manifest['execution_id'],source_sha=manifest['source_sha'],
        runtime_sha=manifest['runtime_sha'],repository=manifest['repository'])
    output=Path(args.output)
    if not request['publication']['enabled']:
        if args.action=='prepare' and os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as file: file.write('recovered=false\n')
        return 0
    identity=identity_for(execution); state=ZodiacState(); youtube=make_client()
    prior=recover(state,youtube,identity,request)
    if args.action=='prepare':
        if prior:
            result=verify(youtube,request,item,identity,prior,prior['render_result'])
            output.mkdir(parents=True,exist_ok=True); (output/'result.json').write_bytes(encoded(result))
        else:
            at=datetime.fromisoformat(item['publish_at'].replace('Z','+00:00'))
            require(at>datetime.now(timezone.utc),'publication slot must be in the future')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as file: file.write('recovered='+str(prior is not None).lower()+'\n')
    else:
        rendered=load(output/'result.json')
        if prior: rendered=prior['render_result']; evidence=prior
        else:
            manifest_video=load(output/'manifest.json')['videos'][0]
            require(manifest_video['id']==item['content_id'] and manifest_video['file']==f"videos/{item['content_id']}.mp4",'artifact identity mismatch')
            evidence=upload(state,youtube,request,item,identity,output/manifest_video['file'],rendered)
        result=verify(youtube,request,item,identity,evidence,rendered)
        (output/'result.json').write_bytes(encoded(result))
    print('Zodiac publishing '+('recovered' if prior else args.action)+' for '+identity['content_id'])
    return 0

if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception as error:
        # Service errors can contain credentials; show only safe, deliberate messages.
        status=getattr(getattr(error,'resp',None),'status',getattr(error,'code',None))
        message=str(error) if isinstance(error,RecoveryBlocked) else type(error).__name__+(f' HTTP {status}' if status is not None else '')+'; inspect/reconcile before retry'
        raise SystemExit('ZODIAC_PUBLISH_FAILED: '+message) from None
