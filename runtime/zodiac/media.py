"""Approved private Zodiac catalogues: native 1080p footage and six-second music."""
from hashlib import sha256
from array import array
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import wave
from urllib.parse import urlparse
from urllib.request import Request, urlopen

def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=180)
    if p.returncode:
        raise ValueError('ZODIAC_MEDIA_COMMAND: '+p.stderr[-500:])
    return p.stdout

def select_assets(backgrounds, audio, content_id):
    digest=sha256(content_id.encode()).digest()
    lists=[backgrounds['assets'],audio['assets']]
    if not all(lists):
        raise ValueError('EMPTY_ZODIAC_CATALOGUE')
    return tuple(a[int.from_bytes(digest[i*4:i*4+4],'big')%len(a)] for i,a in enumerate(lists))

def source(asset, cache, kind):
    ident=asset['id']
    if not re.fullmatch(r'[a-z0-9-]+',ident):
        raise ValueError('INVALID_MEDIA_ID')
    url=asset['download_url'];parsed=urlparse(url)
    allowed={'videos.pexels.com'} if kind=='video' else {'cdn.pixabay.com'}
    if parsed.scheme!='https' or parsed.hostname not in allowed:
        raise ValueError('UNAPPROVED_MEDIA_HOST')
    target=cache/(ident+('.mp4' if kind=='video' else '.mp3'))
    if not target.exists():
        partial=target.with_suffix('.download')
        try:
            with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=60) as response, partial.open('wb') as f:
                total=0
                while chunk:=response.read(1024*1024):
                    total+=len(chunk)
                    if total>256*1024*1024:
                        raise ValueError('MEDIA_DOWNLOAD_TOO_LARGE')
                    f.write(chunk)
            partial.rename(target)
        finally:
            partial.unlink(missing_ok=True)
    return target

def catalogue_paths(root):
    data=Path(root)/'data'
    background=data/'backgrounds.json'
    return background,data/'audio.json'

def prepare(root, content_id, work, cache=None):
    root=Path(root);work=Path(work);work.mkdir(parents=True,exist_ok=True)
    cache=Path(cache) if cache else work/'sources';cache.mkdir(parents=True,exist_ok=True)
    paths=catalogue_paths(root)
    docs=[json.loads(path.read_text()) for path in paths]
    if any(d.get('version')!=1 or d['output']['duration_seconds']!=6 for d in docs):
        raise ValueError('UNSUPPORTED_MEDIA_CATALOGUE')
    background,audio=select_assets(*docs,content_id)
    raw_video=source(background,cache,'video');raw_audio=source(audio,cache,'audio')
    probe=json.loads(run(['ffprobe','-v','error','-select_streams','v:0','-show_streams','-of','json',str(raw_video)]))['streams'][0]
    if (probe['width'],probe['height'])!=(1080,1920):
        raise ValueError('ZODIAC_NATIVE_VERTICAL_1080P_REQUIRED')
    video=work/'background.mp4';music=work/'music.wav'
    v={**docs[0]['loop_defaults'],**background['loop']}
    if not (v['method']=='circular_crossfade' and v['playback_speed']==.5 and v['source_duration_seconds']==3.5 and v['crossfade_seconds']==1 and v['crossfade_curve']=='cosine' and v['blend_position']=='start' and v['playback_direction']=='forward'):
        raise ValueError('UNSUPPORTED_BACKGROUND_LOOP_RECIPE')
    start=float(v['source_start_seconds'])
    graph="[0:v]setpts=2*(PTS-STARTPTS),fps=30,trim=end_frame=30,setpts=PTS-STARTPTS[t];[1:v]setpts=2*(PTS-STARTPTS),fps=30,trim=end_frame=30,setpts=PTS-STARTPTS[h];[t][h]blend=all_expr='A*(1-(1-cos(PI*T))/2)+B*((1-cos(PI*T))/2)'[join];[2:v]setpts=2*(PTS-STARTPTS),fps=30,trim=end_frame=150,setpts=PTS-STARTPTS[b];[join][b]concat=n=2:v=1:a=0,format=yuv420p[v]"
    args=['ffmpeg','-nostdin','-v','error','-y']
    for offset in (3,0,.5):args+=['-threads','2','-ss',str(start+offset),'-i',str(raw_video)]
    run(args+['-filter_complex_threads','1','-filter_complex',graph,'-map','[v]','-an','-frames:v','180','-c:v','libx264','-threads','2','-preset','fast','-crf','20','-movflags','+faststart',str(video)])
    a={**docs[1]['loop_defaults'],**audio['loop']}
    if not (a['method']=='circular_crossfade' and a['source_duration_seconds']==6.25 and a['crossfade_seconds']==.25 and a['crossfade_curve']=='cosine' and a['blend_position']=='start' and a['playback_speed']==1):
        raise ValueError('UNSUPPORTED_AUDIO_LOOP_RECIPE')
    run(['ffmpeg','-nostdin','-v','error','-y','-i',str(raw_audio),'-ss',str(a['source_start_seconds']),'-t','6.25','-ar','48000','-ac','2',str(work/'excerpt.wav')])
    # Work in sample counts: some FFmpeg builds drop a fade-length input.
    with wave.open(str(work/'excerpt.wav')) as wav:
        if (wav.getnframes(),wav.getframerate(),wav.getnchannels(),wav.getsampwidth())!=(300000,48000,2,2):
            raise ValueError('INCOMPLETE_AUDIO_EXCERPT')
        samples=array('h',wav.readframes(300000))
    if sys.byteorder!='little':samples.byteswap()
    joined=[]
    for i in range(12000):
        weight=.5-.5*math.cos(math.pi*i/11999)
        for channel in (0,1):
            joined.append(samples[576000+i*2+channel]*(1-weight)+samples[i*2+channel]*weight)
    joined.extend(samples[24000:576000])
    dc=[sum(joined[c::2])/288000 for c in (0,1)]
    gain=10**(float(a['gain_db'])/20)
    values=[round((value-dc[i%2])*gain) for i,value in enumerate(joined)]
    if max(abs(v) for v in values)>32767:raise ValueError('AUDIO_GAIN_CLIPS')
    pcm=array('h',values)
    if sys.byteorder!='little':pcm.byteswap()
    with wave.open(str(music),'wb') as wav:
        wav.setparams((2,2,48000,288000,'NONE','not compressed'))
        wav.writeframes(pcm.tobytes())
    provenance={'background_id':background['id'],'audio_id':audio['id'],
                'background_source_sha256':sha256(raw_video.read_bytes()).hexdigest(),
                'audio_source_sha256':sha256(raw_audio.read_bytes()).hexdigest(),
                'catalogue_sha256':{path.name:sha256(path.read_bytes()).hexdigest() for path in paths}}
    return video,music,provenance

def encode(card, background, music, mp4):
    # Text mask excludes the black card, preserving the existing full-screen layout.
    graph="[0:v]eq=brightness=-0.06:contrast=0.85[v];[1:v]colorkey=0x000000:0.01:0.0[txt];[v][txt]overlay=0:0:shortest=1,format=yuv420p[out]"
    run(['ffmpeg','-nostdin','-v','error','-y','-threads','2','-i',str(background),'-loop','1','-framerate','30','-i',str(card),'-i',str(music),'-filter_complex_threads','1','-filter_complex',graph,'-map','[out]','-map','2:a:0','-t','6','-c:v','libx264','-threads','2','-preset','fast','-crf','19','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2','-b:a','192k','-movflags','+faststart',str(mp4)])
