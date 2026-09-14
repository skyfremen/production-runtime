"""V1 background registry and immutable request/background validation."""
import json
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]; REGISTRY_PATH=BASE/"data"/"backgrounds.json"; EPS=.05
def load_registry(path=REGISTRY_PATH):
    data=json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data,dict) or data.get("schema_version")!=3 or not isinstance(data.get("assets"),list):
        raise ValueError("Background registry must be schema_version 3 with assets[]")
    return data
def asset_map(data): return {str(a.get("id")):a for a in data.get("assets",[]) if isinstance(a,dict) and a.get("id")}
def validate_request_backgrounds(request,registry):
    if request.get("request_version")!=1: raise ValueError("Only request_version 1 is supported")
    background=request.get("background") or {}; segments=background.get("segments")
    if background.get("mode")!="concatenated_fit_to_short" or not isinstance(segments,list) or len(segments)!=3:
        raise ValueError("Invalid V1 background sequence")
    mapping=asset_map(registry); ids=[]
    for z in segments:
        bid=str(z.get("background_id") or ""); ids.append(bid); a=mapping.get(bid)
        if not a: raise ValueError(f"Unknown background ID: {bid}")
        if a.get("status")!="active" or a.get("verified") is not True or a.get("commercial_use") is not True:
            raise ValueError(f"Background {bid} is not approved for production")
        if a.get("has_embedded_text") is True or a.get("has_watermark") is True:
            raise ValueError(f"Background {bid} contains disallowed text/watermark")
        try: source=float(a["duration_seconds"]); start=float(z["segment_start_seconds"]); duration=float(z["segment_duration_seconds"])
        except (KeyError,TypeError,ValueError): raise ValueError(f"Background {bid} has invalid trusted timing") from None
        if start<0 or duration<60 or start+duration>source+EPS: raise ValueError(f"Background {bid} selected range is invalid")
    if len(set(ids))!=3: raise ValueError("V1 background IDs must be distinct")
    return segments
def main():
    data=load_registry(); print(f"Registry PASS assets={len(data['assets'])}")
if __name__=="__main__": main()
