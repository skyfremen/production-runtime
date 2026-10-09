#!/usr/bin/env python3
"""One-screen Zodiac lists, with optional approved footage and looped music.

Pillow keeps the existing text layout; ffmpeg encodes the six-second MP4.
An explicit offline preview may use black; production always supplies its catalogues.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageStat
from .content import validate

W, H, FPS = 1080, 1920, 30
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
BOLD = str(FONT_DIR/"DejaVuSans-Bold.ttf")
REGULAR = str(FONT_DIR/"DejaVuSans.ttf")
LEFT, RIGHT = 86, 895
BG = (0, 0, 0)
WHITE = (251, 250, 251)
CYAN = (119, 236, 224)
GOLD = (255, 222, 90)
GREEN = (142, 242, 149)

ELEMENTS = (
    ("FIRE", ("aries", "leo", "sagittarius")),
    ("EARTH", ("taurus", "virgo", "capricorn")),
    ("AIR", ("gemini", "libra", "aquarius")),
    ("WATER", ("cancer", "scorpio", "pisces")),
)

class MediaRejected(ValueError):
    pass

def require(value, message):
    if not value:
        raise MediaRejected(message)

def font(size, bold=False):
    path = BOLD if bold else REGULAR
    require(Path(path).exists(), "MISSING_DEJAVU_FONT")
    return ImageFont.truetype(path, size)

def text_width(draw, value, f):
    box = draw.textbbox((0,0), value, font=f)
    return box[2]-box[0]

def title_lines(draw, text):
    for size in (64, 60, 56, 52, 48, 44):
        f=font(size, True)
        result=[]
        for word in text.upper().split():
            current=(result[-1]+" "+word).strip() if result else word
            if result and text_width(draw,current,f) <= RIGHT-LEFT:
                result[-1]=current
            else:
                result.append(word)
            if text_width(draw,word,f)>RIGHT-LEFT:
                break
        if 1 <= len(result) <= 3 and all(text_width(draw,x,f)<=RIGHT-LEFT for x in result):
            return result,size
    raise MediaRejected("TITLE_NOT_READABLE")

def ordered_rows(item):
    rows=item["rows"]
    if item["format"]=="grouped_elements":
        lookup={r["label"].lower():r for r in rows}
        out=[]
        for group, signs in ELEMENTS:
            out.append(("group",group,""))
            for sign in signs:
                r=lookup[sign]
                out.append(("row",r["label"].upper(),r["answer"]))
        return out
    out=[]
    for i,r in enumerate(rows):
        label=r["label"].upper()
        if item["format"]=="ranking":
            label=f"{i+1:02d}. {label}"
        out.append(("row",label,r["answer"]))
    return out

def choose_row_font(draw, entries, row_height):
    maxwidth=RIGHT-LEFT
    for size in range(min(36, int(row_height*.62)), 23, -1):
        f1=font(size,True)
        f2=font(size)
        ok=True
        for kind,label,answer in entries:
            if kind=="group":continue
            w=text_width(draw,label,f1)+22+text_width(draw,answer,f2)
            if w>maxwidth:
                ok=False
                break
        if ok:return size
    raise MediaRejected("ROWS_TOO_LONG_FOR_PHONE_SCREEN")

def render_card(item, path):
    im=Image.new("RGB",(W,H),BG)
    d=ImageDraw.Draw(im)
    lines,title_size=title_lines(d,item["title"])
    y=235
    for line in lines:
        w=text_width(d,line,font(title_size,True))
        d.text(((LEFT+RIGHT-w)//2,y),line,font=font(title_size,True),fill=GOLD)
        y+=int(title_size*1.35)
    y+=33
    d.line((LEFT,y,RIGHT,y),fill=(92,92,92),width=3)
    y+=32
    entries=ordered_rows(item)
    bottom=1515
    row_height=min(81,(bottom-y)/len(entries))
    require(row_height>=43,"TOO_MANY_ROWS_FOR_PHONE")
    size=choose_row_font(d,entries,row_height)
    group_size=min(size+2,30)
    for kind,label,answer in entries:
        if kind=="group":
            d.text((LEFT+4,int(y+3)),label,font=font(group_size,True),fill=GOLD)
        else:
            d.text((LEFT+4,int(y+3)),label,font=font(size,True),fill=WHITE)
            start=LEFT+4+text_width(d,label,font(size,True))+22
            d.text((start,int(y+3)),answer,font=font(size),fill=CYAN)
        y+=row_height
    require(y<=bottom+1,"TEXT_CLIPPED")
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    im.save(path)
    return {"title_lines":len(lines),"title_font_px":title_size,
            "row_font_px":size,"rows":len(entries),
            "content_bottom_y":round(y),"safe_right_x":RIGHT,
            "background":"solid_black","all_text_visible_from_frame_zero":True}

def cmd(args, timeout=120):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    require(p.returncode==0,"COMMAND_FAILED: "+p.stderr[-600:])
    return p.stdout

def qc(mp4, creative, png, layout, *, music=False):
    raw=cmd(["ffprobe","-v","error","-show_entries",
             "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,nb_frames,duration,channels,sample_rate",
             "-of","json",str(mp4)])
    doc=json.loads(raw)
    streams=doc.get("streams",[])
    require(len(streams)==(2 if music else 1) and streams[0].get("codec_type")=="video" and
            (not music or streams[1].get("codec_name")=="aac"),
            "AUDIO_OR_MULTIPLE_STREAMS")
    stream=streams[0]
    require(stream["width"]==W and stream["height"]==H and
            stream["codec_name"]=="h264" and stream["avg_frame_rate"]=="30/1",
            "WRONG_VIDEO_FORMAT")
    dur=float(doc["format"]["duration"])
    require(abs(dur-float(creative["duration_seconds"]))<.06,"WRONG_VIDEO_DURATION")
    if music:
        require(streams[1].get('channels')==2 and streams[1].get('sample_rate')=='48000' and
                abs(float(streams[1].get('duration',0))-dur)<.06,'INCOMPLETE_AUDIO_STREAM')
    frames=int(stream.get("nb_frames",0))
    require(frames==round(dur*FPS),"FRAME_COUNT_MISMATCH")
    require(mp4.stat().st_size>10000,"EMPTY_MP4")
    cmd(["ffmpeg","-v","error","-xerror","-i",str(mp4),"-f","null","-"],timeout=180)
    with Image.open(png) as im:
        require(im.size==(W,H),"WRONG_IMAGE_SIZE")
        # Card must be pure black at all corners, and visibly contain colorful text.
        require(all(im.getpixel(loc)==BG for loc in
                    [(0,0),(W-1,0),(0,H-1),(W-1,H-1)]),
                "NOT_SOLID_BLACK_BACKGROUND")
        top=im.crop((LEFT,235,RIGHT,layout["content_bottom_y"])).convert("L")
        extrema=ImageStat.Stat(top).extrema[0]
        require(extrema[1]-extrema[0]>90,"ILLEGIBLE_CONTRAST")
    return {"technical_qc":"pass","human_review":"still_required",
            "width":W,"height":H,"fps":FPS,"codec":"h264",
            "audio_streams":int(music),"frames":frames,"duration_seconds":dur,
            "size_bytes":mp4.stat().st_size,**layout}

def generate(validated_path, output, *, catalogue_root=None, media_cache=None):
    doc=json.loads(Path(validated_path).read_text(encoding="utf8"))
    require(doc.get("status")=="approved_for_black_preview" and
            doc.get("lane")=="zodiac" and
            doc.get("youtube_upload_enabled") is False and
            len(doc.get("winners",[]))==doc.get("winner_count"),
            "UNAPPROVED_INPUT")
    out=Path(output)
    require(not out.exists(),"ARTIFACT_OUTPUT_ALREADY_EXISTS")
    out.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix="zodiac-black-",dir=str(out.parent)))
    manifest={"lane":"zodiac","background":"solid_black","music":False,
              "youtube_upload_enabled":False,"videos":[]}
    if catalogue_root is not None:
        manifest.update(background="approved_catalogue",music=True)
    report={"passed":False,"concepts":[]}
    try:
        for item in doc["winners"]:
            cid=item["id"]
            png=tmp/"previews"/(cid+".png")
            mp4=tmp/"videos"/(cid+".mp4")
            layout=render_card(item,png)
            mp4.parent.mkdir(parents=True,exist_ok=True)
            provenance={}
            if catalogue_root is not None:
                from .media import prepare, encode
                bg,music,provenance=prepare(catalogue_root,cid,tmp/'media'/cid,cache=media_cache)
                encode(png,bg,music,mp4)
                layout['background']='approved_catalogue'
            else:
                cmd(["ffmpeg","-hide_banner","-loglevel","error","-y",
                 "-loop","1","-framerate",str(FPS),"-i",str(png),
                 "-t",str(item["duration_seconds"]),"-an",
                 "-c:v","libx264","-preset","veryfast","-crf","21",
                 "-pix_fmt","yuv420p","-movflags","+faststart",str(mp4)],timeout=180)
            result=qc(mp4,item,png,layout,music=catalogue_root is not None)
            checksum=sha256(mp4.read_bytes()).hexdigest()
            manifest["videos"].append({"id":cid,"title":item["title"],
                                        "file":f"videos/{cid}.mp4",
                                        "sha256":checksum,**result,**provenance})
            report["concepts"].append({"id":cid,"qc":"pass",
                                       "title":item["title"],
                                       "layout":layout})
        require(len(manifest["videos"])==doc["winner_count"],"INCOMPLETE_BATCH")
        report["passed"]=True
        shutil.rmtree(tmp/'media',ignore_errors=True)
        (tmp/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
        (tmp/"qc.json").write_text(json.dumps(report,indent=2)+"\n")
        tmp.rename(out)
        return {"videos":len(manifest["videos"]),"qc_passed":True,
                "youtube_upload_enabled":False,"artifact":str(out)}
    except BaseException:
        shutil.rmtree(tmp,ignore_errors=True)
        raise

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--validated",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args(argv)
    try:
        print(json.dumps(generate(args.validated,args.output),sort_keys=True))
        return 0
    except (MediaRejected,ValueError,OSError,KeyError,TypeError,subprocess.TimeoutExpired) as e:
        print("ZODIAC_RENDER_REJECTED: "+str(e),file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
