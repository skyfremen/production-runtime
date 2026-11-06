"""Explicit, authenticated recovery of one user-deleted Zodiac upload.

Never clears an unconfirmed/uncertain upload fence. Exact old records are
archived in the same git commit that removes their three active copies.
"""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.request import Request, urlopen
from . import publish
from .contract import blob, encoded, immutable, load, SHA, EX2
from .production import load_execution
from .transport import record_result


def require(ok, message):
    if not ok: raise RuntimeError('Zodiac replacement: '+message)


def authorization(root, request_path, *, expected_execution_id=None):
    require(isinstance(request_path,str) and re.fullmatch(
        r'content/replacements/replace-[A-Za-z0-9-]+\.json',request_path), 'invalid authorization path')
    path=Path(root)/request_path
    require(path.resolve().parent==(Path(root)/'content/replacements').resolve(), 'invalid authorization path')
    raw=path.read_bytes(); data=json.loads(raw)
    require(type(data) is dict and set(data)=={'replacement_version','execution_id','old_video_id','confirmed_deleted','reason'}
        and type(data['replacement_version']) is int and data['replacement_version']==1
        and EX2.fullmatch(str(data['execution_id'])) and publish.VIDEO_ID.fullmatch(str(data['old_video_id']))
        and data['confirmed_deleted'] is True and type(data['reason']) is str
        and 10<=len(data['reason'])<=300 and all(ord(c)>=32 for c in data['reason']), 'invalid authorization')
    if expected_execution_id is not None:
        require(data['execution_id']==expected_execution_id, 'dispatch lock identity mismatch')
    return data,blob(raw)


def require_idle(runs, execution_id):
    for run in runs:
        name=run.get('display_title') or run.get('name','')
        require(not (name=='Zodiac '+execution_id and run.get('status')!='completed'), 'execution is active')


def require_no_marker(youtube, channel, identity):
    uploads=(channel.get('contentDetails',{}).get('relatedPlaylists') or {}).get('uploads')
    require(uploads, 'channel uploads playlist missing')
    ids=[]; page=None
    for _ in range(5):
        response=youtube.playlistItems().list(part='contentDetails',playlistId=uploads,
                                             maxResults=50,pageToken=page).execute()
        require(type(response.get('items')) is list, 'channel scan response invalid')
        for row in response['items']:
            video_id=(row.get('contentDetails') or {}).get('videoId')
            require(publish.VIDEO_ID.fullmatch(str(video_id)), 'channel scan video identity invalid')
            ids.append(video_id)
        page=response.get('nextPageToken')
        if not page: break
    require(not page, 'channel marker scan incomplete; replacement forbidden')
    for start in range(0,len(ids),50):
        response=youtube.videos().list(part='snippet,status',id=','.join(ids[start:start+50]),maxResults=50).execute()
        require(type(response.get('items')) is list, 'marker scan response invalid')
        for video in response['items']:
            snippet=video.get('snippet') or {}
            require(snippet.get('channelId')==channel['id'], 'marker scan channel mismatch')
            require(publish.marker(identity['content_id']) not in (snippet.get('tags') or []),
                    'a live content marker still exists')


def prepare(root, request_path, youtube, *, source_sha, runtime_sha):
    root=Path(root).resolve(); auth,auth_blob=authorization(root,request_path)
    require(SHA.fullmatch(str(source_sha)) and SHA.fullmatch(str(runtime_sha)), 'exact revisions required')
    request,item,execution=load_execution(root,auth['execution_id'],source_sha=source_sha,
                                         runtime_sha=runtime_sha,repository='skyfremen/zodiac-workflow')
    cid=execution['content_id']; archive_path=f'content/replacement-archives/{cid}.json'
    archive=root/archive_path
    if archive.exists():
        prior=load(archive)
        require(prior.get('archive_version')==1 and prior.get('authorization')==auth
                and prior.get('authorization_blob_sha')==auth_blob
                and prior.get('authorization_path')==request_path
                and prior.get('content_id')==cid, 'archive authorization mismatch')
        # The original cleared fence is never cleared a second time.
        return {'archive_path':archive_path,'execution_id':execution['execution_id'],'resumed':True}
    require(request['publication']['enabled'] is True, 'scheduled publication required')
    at=datetime.fromisoformat(item['publish_at'].replace('Z','+00:00'))
    require(at>datetime.now(timezone.utc), 'original publication slot must remain in the future')
    result_path=f'content/results/{cid}.json'; result=load(root/result_path)
    require(result.get('youtube_video_id')==auth['old_video_id'] and
            result.get('execution_id')==execution['execution_id'], 'old video identity mismatch')
    # Uses the existing full provenance, original-video and upload-evidence audit.
    record_result(root,result)
    identity=publish.identity_for(execution)
    channel=publish.channel_for(youtube,request)
    for _ in range(2):
        response=youtube.videos().list(part='id,status',id=auth['old_video_id']).execute()
        require(type(response.get('items')) is list and not response['items'], 'old video still exists or deletion response invalid')
        require_no_marker(youtube,channel,identity)
    paths=[result_path,publish.evidence_path(cid,'intent'),publish.evidence_path(cid,'upload')]
    raw_files={path:(root/path).read_bytes() for path in paths}
    document={'archive_version':1,'content_id':cid,'execution_id':execution['execution_id'],
              'authorization':auth,'authorization_path':request_path,'authorization_blob_sha':auth_blob,
              'source_sha':source_sha,'verified_at':publish.now(),
              'files':{path:{'blob_sha':blob(raw),'base64':base64.b64encode(raw).decode()}
                       for path,raw in raw_files.items()}}
    immutable(archive,document)
    for path in paths: (root/path).unlink()
    return {'archive_path':archive_path,'execution_id':execution['execution_id'],'resumed':False}


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE).decode().strip()


def handoff(root, execution_id, source_sha):
    root=Path(root)
    require(EX2.fullmatch(str(execution_id)) and SHA.fullmatch(str(source_sha)), 'invalid handoff identity')
    execution=load(root/f'content/executions/{execution_id}.json')
    archive=load(root/f"content/replacement-archives/{execution['content_id']}.json")
    require(archive.get('execution_id')==execution_id, 'handoff archive identity mismatch')
    path=f'content/rerender/rerender-replacement-{execution_id}.json'
    immutable(root/path,{'source_sha':source_sha,'execution_ids':[execution_id]})
    return path


def check_active_runs(execution_id):
    token=os.environ.get('GITHUB_TOKEN',''); require(token, 'runtime Actions token missing')
    runs=[]
    for status in ('in_progress','queued','waiting','pending','requested'):
        request=Request('https://api.github.com/repos/skyfremen/production-runtime/actions/workflows/zodiac.yml/runs'
                        f'?status={status}&per_page=100',headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'})
        with urlopen(request,timeout=30) as response: data=json.load(response)
        require(type(data.get('workflow_runs')) is list and data.get('total_count',101)<=100, 'active run scan incomplete')
        runs.extend(data['workflow_runs'])
    require_idle(runs,execution_id)


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument('--root',required=True)
    parser.add_argument('--request',required=True); parser.add_argument('--authorization-source-sha',required=True)
    parser.add_argument('--execution-id',required=True)
    parser.add_argument('--runtime-sha',required=True)
    args=parser.parse_args(argv); root=Path(args.root).resolve()
    require(SHA.fullmatch(args.authorization_source_sha) and SHA.fullmatch(args.runtime_sha), 'exact revisions required')
    # Authorization is pinned; active state is fetched fresh and pushed without force.
    git(root,'fetch','origin','main'); git(root,'checkout','--detach','origin/main')
    head=git(root,'rev-parse','HEAD')
    git(root,'merge-base','--is-ancestor',args.authorization_source_sha,head)
    require(subprocess.check_output(['git','-C',str(root),'show',args.authorization_source_sha+':'+args.request])==(root/args.request).read_bytes(),
            'authorization changed after approval')
    auth,_=authorization(root,args.request,expected_execution_id=args.execution_id)
    check_active_runs(auth['execution_id'])
    out=prepare(root,args.request,publish.make_client(),source_sha=head,runtime_sha=args.runtime_sha)
    if not out['resumed']:
        archive=load(root/out['archive_path']); paths=list(archive['files'])+[out['archive_path']]
        git(root,'add','--',*paths)
        changed=git(root,'diff','--cached','--name-only').splitlines()
        require(set(changed)==set(paths), 'unexpected archive change set')
        git(root,'config','user.name','github-actions[bot]')
        git(root,'config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
        git(root,'commit','-m','[replacement] archive verified deleted Zodiac upload')
        git(root,'push','origin','HEAD:main')
    # Stable across retries even after context/evidence/result commits are added.
    archive_commits=git(root,'log','--diff-filter=A','--format=%H','--',out['archive_path']).splitlines()
    require(len(archive_commits)==1 and SHA.fullmatch(archive_commits[0]), 'archive source commit ambiguous')
    out['source_sha']=archive_commits[0]
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as output:
            for key in ('source_sha','execution_id'): output.write(f'{key}={out[key]}\n')
    print(json.dumps(out,sort_keys=True)); return 0


if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception as exc:
        # API/transport exception bodies can contain credentials; do not print them.
        message=str(exc) if type(exc) is RuntimeError else type(exc).__name__
        raise SystemExit('ZODIAC_REPLACEMENT_BLOCKED: '+message) from None
