"""No-loop fit-to-short treatment for V1 background sequences."""
import subprocess
from pathlib import Path
from resources import media
MODE="concatenated_fit_to_short"; TOL=.30

def duration(path):
    p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)],capture_output=True,text=True)
    try: n=float((p.stdout or "").strip()) if p.returncode==0 else 0
    except ValueError: n=0
    if n<=0: raise RuntimeError("invalid background duration")
    return n

def apply_concatenated_fit_to_short_treatment(target,required_output_duration,caption_score=None,test_mode=False):
    target=Path(target); source=duration(target); out=float(required_output_duration); used=source
    if out<=0: raise RuntimeError("invalid output duration")
    if test_mode and used/out>2.5: used=out*2
    rate=used/out
    if not 1.0-1e-9<=rate<=2.5+1e-9: raise RuntimeError(f"playback rate {rate:.6f}x violates bounds")
    temp=target.with_name(target.name+".fit.mp4"); filters=[]
    if used<source: filters.append(f"trim=start=0:duration={used:.6f}")
    filters += [f"setpts=(PTS-STARTPTS)/{rate:.10f}",f"fps={media.TARGET_FPS}","format=yuv420p"]
    p=subprocess.run(["ffmpeg","-y","-hide_banner","-v","error","-i",str(target),"-map","0:v:0","-vf",",".join(filters),"-an","-sn","-dn","-map_metadata","-1","-c:v","libx264","-preset",media.NORMALIZED_PRESET,"-crf",str(media.NORMALIZED_CRF),str(temp)],capture_output=True,text=True)
    if p.returncode or abs(duration(temp)-out)>TOL: temp.unlink(missing_ok=True); raise RuntimeError("fit-to-short failed")
    actual=duration(temp); temp.replace(target)
    return {"background_treatment_mode":MODE,"background_treatment_derived_playback_rate":round(rate,8),"background_treatment_output_duration_seconds":round(actual,6),"background_treatment_loop_mode":"none","background_treatment_loop_count":0,"readability":media.analyze_caption_region(target,min(actual,out),caption_score)}
