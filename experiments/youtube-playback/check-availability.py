#!/usr/bin/env python3
"""Availability-only YouTube smoke test: NOT proof of video playback."""
import json
import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request
from urllib.error import HTTPError

raw_url = os.environ.get("YOUTUBE_URL") or "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
out_dir = Path("experiments/youtube-playback/artifacts")
out_dir.mkdir(parents=True, exist_ok=True)
report = {"method":"official-youtube-oembed","status":"FAIL","classification":"UNKNOWN","url":raw_url,
          "video_id":None,"title":None,"thumbnail_status":None,"note":"Metadata availability is not playback."}
try:
    parsed = urllib.parse.urlsplit(raw_url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.youtube.com","youtube.com","m.youtube.com","youtu.be"):
        raise ValueError("Only HTTPS YouTube URLs are accepted")
    video_id = parsed.path.strip("/") if parsed.hostname == "youtu.be" else (
        parsed.path.strip("/").split("/")[1] if parsed.path.startswith(("/shorts/","/live/")) else
        urllib.parse.parse_qs(parsed.query).get("v",[None])[0] if parsed.path == "/watch" else None
    )
    if not video_id or len(video_id)!=11 or not all(c.isalnum() or c in "-_" for c in video_id):
        raise ValueError("Invalid YouTube video ID")
    report["video_id"] = video_id
    query = urllib.parse.urlencode({"url":"https://www.youtube.com/watch?v="+video_id,"format":"json"})
    request = urllib.request.Request("https://www.youtube.com/oembed?"+query,
        headers={"User-Agent":"YouTube-QA/1.0"})
    with urllib.request.urlopen(request,timeout=20) as response:
        info = json.load(response)
    title = info.get("title")
    if not title or info.get("provider_name") != "YouTube":
        raise RuntimeError("Unexpected YouTube oEmbed response")
    report["title"] = title
    report["classification"] = "METADATA_ACCESSIBLE"
    report["status"] = "PASS"
    report["note"] = "The public YouTube metadata endpoint resolves this video; stream playback was NOT verified."
    thumbnail = info.get("thumbnail_url")
    if thumbnail:
        thumburl = urllib.parse.urlsplit(thumbnail)
        if thumburl.scheme == "https" and thumburl.hostname in ("i.ytimg.com","img.youtube.com"):
            try:
                thumbreq = urllib.request.Request(thumbnail,method="HEAD",headers={"User-Agent":"YouTube-QA/1.0"})
                with urllib.request.urlopen(thumbreq, timeout=10) as response:
                    report["thumbnail_status"] = response.status
            except Exception as exc:
                report["thumbnail_status"] = "inaccessible: "+str(exc)[:120]
except HTTPError as exc:
    report["classification"] = "HTTP_"+str(exc.code)
    report["error"] = str(exc)
except Exception as exc:
    report["classification"] = "CHECK_FAILED"
    report["error"] = str(exc)
finally:
    (out_dir/"availability-report.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
sys.exit(0 if report["status"]=="PASS" else 1)
