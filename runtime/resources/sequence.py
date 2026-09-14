"""Resolve and concatenate the three immutable V1 background clips."""
import subprocess
from pathlib import Path
from base.contract import OUTPUT_DIR, atomic_write_json, load_json
from resources import media
from resources.fit import MODE, TOL, duration
from resources.validate import load_registry, asset_map, validate_request_backgrounds

def get(asset,seg,target,download,preflight):
    choices=list(media.suitable_renditions(asset)); fallback=media.generic_fallback(asset)
    if fallback: choices.append(fallback)
    for r in choices:
        if preflight and not media.preflight(r["direct_url"])[0]: continue
        if download:
            try: media.download(asset,r,target,segment_duration_seconds=float(seg["segment_duration_seconds"]))
            except RuntimeError: continue
            if float(seg["segment_start_seconds"])+float(seg["segment_duration_seconds"])>duration(target)+.05:
                Path(target).unlink(missing_ok=True); continue
        return Path(target)
    raise RuntimeError(f"background {asset.get('id')} has no executable rendition")

def assemble(paths,segs,target):
    cmd=["ffmpeg","-y","-hide_banner","-v","error"]
    for p in paths: cmd += ["-i",str(p)]
    fs=[]; labels=[]
    for i,s in enumerate(segs):
        a=float(s["segment_start_seconds"]); d=float(s["segment_duration_seconds"]); label=f"v{i}"; labels.append(f"[{label}]")
        fs.append(f"[{i}:v:0]trim=start={a:.6f}:duration={d:.6f},setpts=PTS-STARTPTS,fps={media.TARGET_FPS},format=yuv420p[{label}]")
    fs.append("".join(labels)+f"concat=n=3:v=1:a=0,fps={media.TARGET_FPS},format=yuv420p[outv]")
    cmd += ["-filter_complex",";".join(fs),"-map","[outv]","-an","-sn","-dn","-map_metadata","-1","-c:v","libx264","-preset",media.NORMALIZED_PRESET,"-crf",str(media.NORMALIZED_CRF),str(target)]
    p=subprocess.run(cmd,capture_output=True,text=True)
    if p.returncode: raise RuntimeError("background concatenation failed")
    if abs(duration(target)-sum(float(s["segment_duration_seconds"]) for s in segs))>TOL: raise RuntimeError("background duration mismatch")

def resolve(request_path,registry_path=None,do_download=True,do_preflight=True):
    request=load_json(request_path); reg=load_registry(registry_path or "runtime/data/backgrounds.json"); segs=validate_request_backgrounds(request,reg); m=asset_map(reg); paths=[]
    try:
        for i,s in enumerate(segs): paths.append(get(m[s["background_id"]],s,OUTPUT_DIR/f"background.segment.{i}.asset",do_download,do_preflight))
        if do_download: assemble(paths,segs,OUTPUT_DIR/"background.asset")
        result={"background_selection":"selected","background_sequence":segs,"background_treatment_pending":bool(do_download),"metrics":{"background_treatment_mode":MODE,"background_treatment_loop_mode":"none","background_treatment_loop_count":0}}
        atomic_write_json(OUTPUT_DIR/"background_selection.json",result); return result
    except Exception:
        for p in paths: p.unlink(missing_ok=True)
        (OUTPUT_DIR/"background.asset").unlink(missing_ok=True); raise
