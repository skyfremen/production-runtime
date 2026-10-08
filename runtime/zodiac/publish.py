"""Future opt-in Zodiac publishing; independent credentials and durable upload fence."""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import json
import os
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from .lifecycle import REPOSITORY, encoded, EXECUTION

class PublishRejected(ValueError): pass

def require(ok,reason):
    if not ok: raise PublishRejected(reason)

def valid_channel_id(value):
    return isinstance(value,str) and bool(re.fullmatch(r'UC[A-Za-z0-9_-]{22}',value)) and value!='UCvrq2m9G4yrwPfL_X-QPzMA'

def check_channel(response,expected):
    items=response.get('items',[])
    require(valid_channel_id(expected) and len(items)==1 and items[0].get('id')==expected,'ZODIAC_CHANNEL_MISMATCH')
    return expected

def upload_action(state):
    if state is None: return None
    require(state.get('state')=='uploaded' and re.fullmatch(r'[A-Za-z0-9_-]{11}',str(state.get('video_id'))),'UPLOAD_RESERVED_NO_SECOND_INSERT')
    return state['video_id']

class Journal:
    def __init__(self,execution_id):
        require(EXECUTION.fullmatch(execution_id),'UPLOAD_EXECUTION_ID')
        require(os.environ.get('GITHUB_REPOSITORY')==REPOSITORY,'UPLOAD_REPOSITORY')
        self.token=os.environ.get('GITHUB_TOKEN'); require(bool(self.token),'UPLOAD_STATE_TOKEN')
        self.url=f'https://api.github.com/repos/{REPOSITORY}/contents/content/uploads/{execution_id}.json'
        self.sha=None
    def read(self):
        req=Request(self.url,headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json'})
        try:
            with urlopen(req,timeout=45) as response: data=json.load(response)
        except HTTPError as exc:
            if exc.code==404: return None
            raise PublishRejected(f'UPLOAD_STATE_READ_HTTP_{exc.code}') from None
        self.sha=data['sha']
        return json.loads(base64.b64decode(data['content']))
    def write(self,state):
        data={'message':'[zodiac upload] persist execution reservation','branch':'main','content':base64.b64encode(encoded(state)).decode()}
        if self.sha: data['sha']=self.sha
        req=Request(self.url,data=json.dumps(data).encode(),method='PUT',headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json','Content-Type':'application/json'})
        try:
            with urlopen(req,timeout=45) as response: result=json.load(response)
        except HTTPError as exc: raise PublishRejected(f'UPLOAD_STATE_WRITE_HTTP_{exc.code}') from None
        self.sha=result['content']['sha']

def make_client():
    names=('ZODIAC_CLIENT_ID','ZODIAC_CLIENT_SECRET','ZODIAC_REFRESH_TOKEN')
    require(all(os.environ.get(k) for k in names),'ZODIAC_CREDENTIALS_REQUIRED')
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    credentials=Credentials(None,token_uri='https://oauth2.googleapis.com/token',client_id=os.environ[names[0]],
        client_secret=os.environ[names[1]],refresh_token=os.environ[names[2]])
    return build('youtube','v3',credentials=credentials,cache_discovery=False)

def publish(request,item,mp4,execution,source_sha):
    require(request['publication']['enabled'] is True,'PUBLICATION_DISABLED')
    expected=request['publication']['channel_id']; youtube=make_client()
    check_channel(youtube.channels().list(part='id',mine=True).execute(),expected)
    journal=Journal(execution['execution_id']); old=journal.read()
    identity={'execution_id':execution['execution_id'],'content_id':execution['content_id'],
              'request_blob_sha':execution['request_blob_sha'],'source_sha':source_sha,'channel_id':expected}
    if old is not None:
        require(all(old.get(k)==v for k,v in identity.items()),'UPLOAD_STATE_IDENTITY')
    video_id=upload_action(old)
    if video_id is None:
        when=datetime.fromisoformat(item['publish_at'].replace('Z','+00:00'))
        require(when>datetime.now(timezone.utc),'PUBLICATION_SLOT_EXPIRED')
        # Persist before the first insertion. A crash or uncertain response blocks
        # automatic insertion on rerun; it cannot produce a duplicate video.
        journal.write({**identity,'state':'reserved','video_id':None})
        from googleapiclient.http import MediaFileUpload
        marker='zodiac-execution:'+execution['execution_id']
        body={'snippet':{'title':item['winner']['title'],'description':'Playful Zodiac stereotypes for entertainment.\n'+marker,
                         'tags':['zodiac','astrology',execution['execution_id']],'categoryId':'24'},
              'status':{'privacyStatus':'private','publishAt':item['publish_at'],'selfDeclaredMadeForKids':False}}
        response=youtube.videos().insert(part='snippet,status',body=body,media_body=MediaFileUpload(str(mp4),mimetype='video/mp4',resumable=True)).execute()
        video_id=response.get('id'); require(re.fullmatch(r'[A-Za-z0-9_-]{11}',str(video_id)),'UPLOAD_VIDEO_ID')
        journal.write({**identity,'state':'uploaded','video_id':video_id})
    records=youtube.videos().list(part='snippet,status',id=video_id).execute().get('items',[])
    require(len(records)==1 and records[0]['snippet'].get('channelId')==expected and
        records[0]['snippet'].get('title')==item['winner']['title'],'UPLOADED_VIDEO_IDENTITY')
    status=records[0].get('status',{})
    require(status.get('uploadStatus')=='processed' and status.get('privacyStatus')=='private' and
        datetime.fromisoformat(status.get('publishAt','').replace('Z','+00:00'))==datetime.fromisoformat(item['publish_at'].replace('Z','+00:00')),'UPLOAD_VERIFICATION_PENDING_OR_FAILED')
    return video_id
