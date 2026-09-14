"""Canonical Wacky Dramas production request V1 validator."""
import argparse, json, re
from pathlib import Path
from base.contract import ensure_request_path_matches
MIN_BG=60.0; MAX_BG=100.0; MIN_TOTAL=180.0; MAX_TOTAL=300.0; TTS_SPEED=1.75
NATURAL={"natural","neutral","conversational","warm","calm"}
EXPRESSIVE={"expressive","dramatic","comedy","sarcastic","dramatic_comedy","absurd"}
TONES=NATURAL|EXPRESSIVE
def mapped_voice(g,t):
    if g not in {"female","male"}: raise ValueError("story.lead_gender must be female or male")
    if t not in TONES: raise ValueError("story.story_tone is not approved")
    return ("af_bella" if t in EXPRESSIVE else "af_heart") if g=="female" else ("am_fenrir" if t in EXPRESSIVE else "am_echo")
def nonempty(v,name):
    x=str(v or "").strip()
    if not x: raise ValueError(f"{name} must be non-empty")
    return x
def validate_request_data(x):
    top={"request_version","content_id","source_draft_id","channel","story","narration","background","youtube","visibility","render"}
    if not isinstance(x,dict) or set(x)!=top: raise ValueError("request must contain exactly the V1 top-level fields")
    if x["request_version"]!=1: raise ValueError("request_version must be 1")
    if not re.fullmatch(r"wd-[0-9a-f]{24}",str(x["content_id"])): raise ValueError("invalid content_id")
    if not re.fullmatch(r"draft-[A-Za-z0-9-]{8,96}",str(x["source_draft_id"])): raise ValueError("invalid source_draft_id")
    if x["channel"]!={"name":"Wacky Dramas","handle":"@WACKYDRAMAS"}: raise ValueError("invalid channel")
    s=x["story"]
    required_story={"category","premise","conflict","twist","hook","script","lead_gender","story_tone","card_emojis"}
    allowed_story=required_story|{"punchline"}
    if not isinstance(s,dict) or not required_story<=set(s) or set(s)-allowed_story:
        raise ValueError("story must contain the required V1 fields; punchline is optional")
    for k in required_story-{"card_emojis"}: nonempty(s[k],"story."+k)
    if not isinstance(s["card_emojis"],list) or not 4<=len(s["card_emojis"])<=6: raise ValueError("story.card_emojis must contain 4-6 entries")
    expected_voice=mapped_voice(str(s["lead_gender"]),str(s["story_tone"]))
    if x["narration"]!={"engine":"kokoro","voice":expected_voice,"speed":TTS_SPEED}: raise ValueError("invalid deterministic narration contract")
    b=x["background"]
    if not isinstance(b,dict) or set(b)!={"mode","segments"} or b["mode"]!="concatenated_fit_to_short": raise ValueError("invalid background contract")
    seg=b["segments"]
    if not isinstance(seg,list) or len(seg)!=3: raise ValueError("background.segments must contain exactly 3 clips")
    ids=[]; total=0.0
    for i,z in enumerate(seg):
        if not isinstance(z,dict) or set(z)!={"background_id","segment_start_seconds","segment_duration_seconds"}: raise ValueError(f"invalid background segment {i}")
        ids.append(nonempty(z["background_id"],f"background.segments[{i}].background_id"))
        start=float(z["segment_start_seconds"]); duration=float(z["segment_duration_seconds"])
        if abs(start)>1e-6 or not MIN_BG<=duration<=MAX_BG: raise ValueError(f"invalid background segment timing {i}")
        total+=duration
    if len(set(ids))!=3: raise ValueError("background IDs must be distinct")
    if not MIN_TOTAL<=total<=MAX_TOTAL: raise ValueError("background source duration must be 180-300 seconds")
    y=x["youtube"]; yk={"title","description","hashtags","tags","category_id","made_for_kids"}
    if not isinstance(y,dict) or set(y)!=yk: raise ValueError("invalid youtube contract")
    if not 1<=len(nonempty(y["title"],"youtube.title"))<=100: raise ValueError("youtube.title exceeds 100 characters")
    nonempty(y["description"],"youtube.description")
    if not isinstance(y["hashtags"],list) or not isinstance(y["tags"],list) or y["made_for_kids"] is not False: raise ValueError("invalid youtube metadata")
    if x["visibility"]!="public": raise ValueError("V1 visibility must be public")
    forbidden={"slot","publish_at","schedule_date","schedule_time","reserved_hour"}
    def walk(v):
        if isinstance(v,dict):
            if forbidden&set(v): raise ValueError("V1 request contains forbidden scheduling fields")
            for c in v.values(): walk(c)
        elif isinstance(v,list):
            for c in v: walk(c)
    walk(x)
    expected={"width":1080,"height":1920,"fps":30,"video_codec":"h264","h264_profile":"high","pixel_format":"yuv420p",
              "audio_codec":"aac","audio_sample_rate":48000,"background_music":False}
    if x["render"]!=expected: raise ValueError("invalid render contract")
    return x
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--request",required=True); a=ap.parse_args()
    data=json.loads(Path(a.request).read_text(encoding="utf-8")); ensure_request_path_matches(a.request,data); validate_request_data(data)
    print("Request schema PASS")
if __name__=="__main__": main()
