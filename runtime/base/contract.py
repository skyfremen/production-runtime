import hashlib, json, os, re
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
REQUESTS_DIR=BASE/"content"/"requests"; RESULTS_DIR=BASE/"content"/"results"
OUTPUT_DIR=Path(os.getenv("STORY_OUTPUT_DIR",str(BASE/"output")))
CONTENT_ID_RE=re.compile(r"^wd-[0-9a-f]{24}$"); EXECUTION_ID_RE=re.compile(r"^ex-[0-9a-f]{24}$")
PRODUCTION_MAX_SECONDS=178.0; PRODUCTION_TARGET_MIN_SECONDS=120.0; PRODUCTION_TARGET_MAX_SECONDS=175.0
START_LEAD_SECONDS=0.50; END_TAIL_SECONDS=0.35; PRODUCTION_ENCODE_SAFETY_SECONDS=0.10
YOUTUBE_TAG_MAX_CHARS=30; EXPECTED_YOUTUBE_CHANNEL_ID="UCvrq2m9G4yrwPfL_X-QPzMA"
DEFAULT_VIDEO_WIDTH=1080; DEFAULT_VIDEO_HEIGHT=1920; DEFAULT_VIDEO_FPS=30
def load_json(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def atomic_write_json(path,payload):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); tmp.replace(path)
def request_content_id(data): return str(data.get("content_id","")).strip()
def validate_content_id(content_id):
    if not CONTENT_ID_RE.fullmatch(str(content_id or "")): raise ValueError("content_id must match wd- plus 24 lowercase hex")
    return content_id
def marker_tag(content_id):
    validate_content_id(content_id); marker="wd-id-"+hashlib.sha256(content_id.encode()).hexdigest()[:20]
    if len(marker)>YOUTUBE_TAG_MAX_CHARS: raise AssertionError("marker exceeds tag budget")
    return marker
def request_path_for_id(content_id): validate_content_id(content_id); return REQUESTS_DIR/f"{content_id}.json"
def ensure_request_path_matches(path,data):
    cid=request_content_id(data); validate_content_id(cid)
    if Path(path).name!=cid+".json": raise ValueError(f"Request filename must be {cid}.json")
    return cid
def env_bool(name,default=False):
    raw=os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1","true","yes","on"}
def expected_video_config():
    return {"width":int(os.getenv("VIDEO_WIDTH",str(DEFAULT_VIDEO_WIDTH))),
            "height":int(os.getenv("VIDEO_HEIGHT",str(DEFAULT_VIDEO_HEIGHT))),
            "fps":int(os.getenv("VIDEO_FPS",str(DEFAULT_VIDEO_FPS)))}
