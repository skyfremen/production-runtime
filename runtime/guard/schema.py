"""Canonical Wacky Dramas scheduled production request V2 validator."""
import argparse, json, re
from datetime import datetime
from pathlib import Path
from base.contract import ensure_request_path_matches

MIN_BG=60.0; MAX_BG=100.0; MIN_TOTAL=180.0; MAX_TOTAL=300.0; TTS_SPEED=1.75
NATURAL={"natural","neutral","conversational","warm","calm"}
EXPRESSIVE={"expressive","dramatic","comedy","sarcastic","dramatic_comedy","absurd"}
TONES=NATURAL|EXPRESSIVE
HOOK_TYPES={"accusation","discovery","contradiction","money_stakes","social_exposure","urgency","confession","consequence_first"}

def mapped_voice(g,t):
    if g not in {"female","male"}: raise ValueError("story.lead_gender must be female or male")
    if t not in TONES: raise ValueError("story.story_tone is not approved")
    return ("af_bella" if t in EXPRESSIVE else "af_heart") if g=="female" else ("am_fenrir" if t in EXPRESSIVE else "am_echo")

def nonempty(v,name):
    x=str(v or "").strip()
    if not x: raise ValueError(f"{name} must be non-empty")
    return x

def instant(v,name):
    try: value=datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except ValueError: raise ValueError(f"{name} must be RFC3339") from None
    if value.tzinfo is None: raise ValueError(f"{name} must include timezone")
    return value

def validate_trend_metadata(story):
    has_aware="trend_aware" in story; has_topic="trend_topic" in story
    if has_aware != has_topic:
        raise ValueError("story trend metadata must contain both trend_aware and trend_topic or neither")
    if not has_aware:
        return
    aware=story["trend_aware"]; topic=story["trend_topic"]
    if type(aware) is not bool:
        raise ValueError("story.trend_aware must be boolean")
    if aware:
        if not isinstance(topic,str) or not topic.strip():
            raise ValueError("story.trend_topic must be non-empty when trend_aware is true")
    elif topic is not None:
        raise ValueError("story.trend_topic must be null when trend_aware is false")

def validate_request_data(x):
    top={"request_version","content_id","source_draft_id","channel","story","narration","background","youtube","publication","visibility","render"}
    if not isinstance(x,dict) or set(x)!=top: raise ValueError("request must contain exactly the V2 top-level fields")
    if x["request_version"]!=2: raise ValueError("request_version must be 2")
    if not re.fullmatch(r"wd-[0-9a-f]{24}",str(x["content_id"])): raise ValueError("invalid content_id")
    if not re.fullmatch(r"draft-[A-Za-z0-9-]{8,96}",str(x["source_draft_id"])): raise ValueError("invalid source_draft_id")
    if x["channel"]!={"name":"Wacky Dramas","handle":"@WACKYDRAMAS"}: raise ValueError("invalid channel")
    s=x["story"]
    required_story={"category","premise","conflict","twist","hook","script","lead_gender","story_tone","card_emojis"}
    allowed_story=required_story|{"punchline","hook_type","trend_aware","trend_topic","like_cta"}
    if not isinstance(s,dict) or not required_story<=set(s) or set(s)-allowed_story:
        raise ValueError("story must contain the required V2 fields; punchline and creative provenance are optional")
    for k in required_story-{"card_emojis"}: nonempty(s[k],"story."+k)
    if "hook_type" in s and s["hook_type"] not in HOOK_TYPES:
        raise ValueError("story.hook_type must be a supported hook type")
    if "like_cta" in s:
        like_cta=nonempty(s["like_cta"],"story.like_cta")
        if len(like_cta)>80 or "\n" in like_cta or "\r" in like_cta:
            raise ValueError("story.like_cta must be a single line of at most 80 characters")
    validate_trend_metadata(s)
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
    p=x["publication"]
    if not isinstance(p,dict) or set(p)!={"mode","publish_at"} or p.get("mode")!="scheduled": raise ValueError("invalid scheduled publication contract")
    scheduled=instant(p.get("publish_at"),"publication.publish_at")
    if scheduled.minute not in {0,20,40} or scheduled.second or scheduled.microsecond: raise ValueError("publication.publish_at must be on minute 00, 20, or 40")
    if x["visibility"]!="private": raise ValueError("V2 visibility must be private before scheduled publication")
    expected={"width":1080,"height":1920,"fps":30,"video_codec":"h264","h264_profile":"high","pixel_format":"yuv420p",
              "audio_codec":"aac","audio_sample_rate":48000,"background_music":False}
    if x["render"]!=expected: raise ValueError("invalid render contract")
    return x

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--request",required=True); a=ap.parse_args()
    data=json.loads(Path(a.request).read_text(encoding="utf-8")); ensure_request_path_matches(a.request,data); validate_request_data(data)
    print("Request schema PASS")

if __name__=="__main__": main()
