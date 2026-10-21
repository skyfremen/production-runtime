#!/usr/bin/env python3
"""Decode a few seconds of a public YouTube video using yt-dlp + ffmpeg."""
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit, parse_qs

DIR = Path('experiments/youtube-playback/artifacts')
DIR.mkdir(parents=True, exist_ok=True)
url = os.getenv('YOUTUBE_URL') or 'https://www.youtube.com/watch?v=M7lc1UVf-VE'
result = {'method':'yt-dlp-ffmpeg-decode','url':url,'status':'FAIL','reason':'','video_frames':0}

def command(args, timeout=90):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout)

try:
    u = urlsplit(url)
    if u.scheme != 'https' or u.hostname not in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be'):
        raise ValueError('Expected an HTTPS YouTube URL')
    if not (u.hostname == 'youtu.be' or u.path.startswith('/shorts/') or u.path.startswith('/live/') or u.path == '/watch'):
        raise ValueError('Expected a YouTube video URL')

    # Only request the first five seconds of the sample. No cookies, proxies or credentials.
    output = str(DIR / 'segment.%(ext)s')
    cmd = [
        sys.executable, '-m', 'yt_dlp',
        '--ignore-config', '--no-playlist', '--no-progress',
        '--socket-timeout', '12', '--retries', '1', '--fragment-retries', '1',
        '--download-sections', '*0-5',
        '--format', 'best[ext=mp4]/best',
        '--force-overwrites', '--no-part',
        '-o', output, url
    ]
    download = command(cmd,timeout=105)
    print('YT_DLP_LOG:', (download.stdout + '\n' + download.stderr)[-3000:])
    if download.returncode != 0:
        raise RuntimeError('yt-dlp stream download failed (exit ' + str(download.returncode) + ')')

    videos = [p for p in DIR.glob('segment.*') if p.suffix.lower() in ('.mp4','.webm','.mkv')]
    if not videos: raise RuntimeError('No playable media file was produced')
    file = max(videos, key=lambda p:p.stat().st_size)
    # Only mark success after actual video frame decoding, never just metadata discovery.
    decoded = command(['ffmpeg','-hide_banner','-v','error','-i',str(file),'-t','3',
                       '-an','-f','null','-','-progress','pipe:1'],timeout=40)
    if decoded.returncode != 0:
        raise RuntimeError('ffmpeg could not decode media: '+decoded.stderr[-1000:])
    for line in decoded.stdout.splitlines():
        if line.startswith('frame='):
            try: result['video_frames'] = int(line.split('=',1)[1])
            except ValueError: pass
    if result['video_frames'] < 15:
        raise RuntimeError('Decoded fewer than 15 video frames: '+str(result['video_frames']))
    result['status'] = 'PASS'
    result['reason'] = 'Downloaded a 5-second stream segment and decoded '+str(result['video_frames'])+' frames'
    result['segment_bytes'] = file.stat().st_size
    print('PASS:',result['reason'])
    file.unlink(missing_ok=True)
except Exception as exc:
    result['reason'] = str(exc)
    print('FAIL:',result['reason'])
finally:
    (DIR / 'stream-report.json').write_text(json.dumps(result, indent=2))
    print('STREAM_RESULT:',json.dumps(result))
sys.exit(0 if result['status']=='PASS' else 1)
