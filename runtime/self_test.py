"""Offline structural and deterministic self-test for Wacky Dramas V2."""
import json
from pathlib import Path
from base.compat import CONTRACT, contract_hash, validate_contract_hash
from guard.schema import validate_request_data
from output.state import evidence_path, result_path
from output.transfer import build_upload_body, prepare_upload
from transform.semantic import resolve_semantic_span
ROOT=Path(__file__).resolve().parents[1]; checks=0

def ok(condition,name):
    global checks
    if not condition: raise AssertionError(name)
    checks+=1; print("PASS",name)

def sample_request():
    script_words=["word"]*360; payoff="the receipt proved everything"; script_words[200:205]=payoff.split()
    return {"request_version":2,"content_id":"wd-"+"a"*24,"source_draft_id":"draft-selftest01","channel":{"name":"Wacky Dramas","handle":"@WACKYDRAMAS"},
      "story":{"category":"work","premise":"A coworker steals credit.","conflict":"The liar gets praised.","twist":"A timestamped receipt exists.","hook":"Everyone believed the wrong person.","script":" ".join(script_words),"lead_gender":"female","story_tone":"dramatic","punchline":payoff,"card_emojis":["😳","💬","🔥","👀"],"trend_aware":True,"trend_topic":"GTA 6"},
      "narration":{"engine":"kokoro","voice":"af_bella","speed":1.75},
      "background":{"mode":"concatenated_fit_to_short","segments":[{"background_id":"a","segment_start_seconds":0.0,"segment_duration_seconds":60.0},{"background_id":"b","segment_start_seconds":0.0,"segment_duration_seconds":60.0},{"background_id":"c","segment_start_seconds":0.0,"segment_duration_seconds":60.0}]},
      "youtube":{"title":"The Receipt Changed Everything","description":"A workplace story.","hashtags":["#WackyDramas","#Shorts"],"tags":["Wacky Dramas","Shorts"],"category_id":"24","made_for_kids":False},
      "publication":{"mode":"scheduled","publish_at":"2030-01-01T00:00:00Z"},"visibility":"private",
      "render":{"width":1080,"height":1920,"fps":30,"video_codec":"h264","h264_profile":"high","pixel_format":"yuv420p","audio_codec":"aac","audio_sample_rate":48000,"background_music":False}}

class EmptyState:
    def __init__(self): self.created=[]
    def load(self,_path): return None
    def create(self,path,data): self.created.append((path,data)); raise AssertionError("prepare_upload must not create the irreversible intent")
class Channels:
    def list(self,**_kwargs): return self
    def execute(self): return {"items":[{"id":"UCvrq2m9G4yrwPfL_X-QPzMA","contentDetails":{"relatedPlaylists":{"uploads":"PL"}}}]}
class FakeYoutube:
    def channels(self): return Channels()

def main():
    request=sample_request(); validate_request_data(request); ok(True,"v2 scheduled request schema")
    for label,value in (("missing",None),("null",None),("empty",""),("unmatched","this phrase is absent")):
        q=json.loads(json.dumps(request))
        if label=="missing": q["story"].pop("punchline")
        else: q["story"]["punchline"]=value
        validate_request_data(q); ok(True,f"{label} punchline is non-blocking")
    legacy=json.loads(json.dumps(request)); legacy["story"].pop("trend_aware"); legacy["story"].pop("trend_topic"); validate_request_data(legacy); ok(True,"legacy request without trend provenance remains valid")
    evergreen=json.loads(json.dumps(request)); evergreen["story"]["trend_aware"]=False; evergreen["story"]["trend_topic"]=None; validate_request_data(evergreen); ok(True,"evergreen trend provenance validates")
    bad=json.loads(json.dumps(request)); bad["story"]["trend_aware"]=True; bad["story"]["trend_topic"]=None
    try: validate_request_data(bad)
    except ValueError: ok(True,"trend-aware request requires topic")
    else: raise AssertionError("trend-aware request without topic must fail")
    bad=json.loads(json.dumps(request)); bad["story"]["trend_aware"]=False; bad["story"]["trend_topic"]="GTA 6"
    try: validate_request_data(bad)
    except ValueError: ok(True,"evergreen request rejects topic")
    else: raise AssertionError("evergreen request with topic must fail")
    words=[{"word":word,"start":i*.1,"end":(i+1)*.1} for i,word in enumerate("before the receipt proved everything after".split())]
    semantic=resolve_semantic_span(words,"the receipt proved everything"); ok(semantic["status"]=="matched" and semantic["punchline_indices"]==frozenset({1,2,3,4}),"v2 string punchline resolves")
    ok(contract_hash()=="db118b20737d06509071754851388e51af427b7930cd48708b3e427415fce1de","single compatibility hash"); validate_contract_hash(contract_hash())
    try: validate_contract_hash("0"*64)
    except ValueError: ok(True,"compatibility mismatch fails")
    else: raise AssertionError("compatibility mismatch must fail")
    body=build_upload_body(request); ok(body["status"]["privacyStatus"]=="private" and body["status"]["publishAt"]==request["publication"]["publish_at"],"scheduled PRIVATE body")
    immediate=json.loads(json.dumps(request)); immediate.pop("publication")
    try: validate_request_data(immediate)
    except ValueError: ok(True,"publication is required")
    else: raise AssertionError("missing publication must fail")
    public=json.loads(json.dumps(request)); public["visibility"]="public"
    try: validate_request_data(public)
    except ValueError: ok(True,"immediate public contract rejected")
    else: raise AssertionError("public visibility must fail")
    ok(evidence_path(request["content_id"],"intent").startswith("content/executions/evidence/"),"execution evidence path"); ok(result_path(request["content_id"])==f"content/results/{request['content_id']}.json","immutable result path"); ok(CONTRACT["request_path"]=="content/requests/{request_id}.json","batch contract descriptor")
    single=(ROOT/".github/workflows/single.yml").read_text(); ok(all(n in single for n in ("execution_id","source_sha")) and "contract_hash:" not in single and "dispatch_id:" not in single,"two-field opaque dispatch inputs"); ok("narration" not in single and "request_json" not in single,"no full request workflow input")
    pipeline_source=(ROOT/"runtime/engine/pipeline.py").read_text(); ok("runtime/guard/schema.py" not in pipeline_source and "validate_request_backgrounds" not in pipeline_source,"single production preflight"); ok(not (ROOT/"runtime/guard/semantic.py").exists(),"legacy semantic guard removed")
    state=EmptyState(); decision=prepare_upload(state,"runtime/content/requests/"+request["content_id"]+".json",request,{"content_id":request["content_id"],"request_path":f"content/requests/{request['content_id']}.json","request_blob_sha":"a"*40,"source_commit_sha":"b"*40},FakeYoutube()); ok(decision["upload_required"] is True and not state.created,"intent not created during render preparation")
    transfer=(ROOT/"runtime/output/transfer.py").read_text(); ok(transfer.index('state.create(evidence_path(identity["content_id"],"intent")')<transfer.index("youtube.videos().insert"),"durable intent precedes videos.insert"); ok("duplicate upload forbidden" in transfer,"ambiguous upload blocks"); ok('"privacyStatus":"private"' in transfer and '"publishAt"' in transfer,"transfer schedules private upload")
    result=(ROOT/"runtime/output/result.py").read_text().replace(" ",""); ok('"status":"scheduled"' in result and '"visibility":"private"' in result,"result requires verified schedule")
    verify=(ROOT/"runtime/output/verify.py").read_text(); ok("verified_scheduled" in verify and "publishAt differs" in verify,"remote publishAt verified")
    resolver=(ROOT/"runtime/resources/resolve.py").read_text(); ok(resolver.count("setsar=1")>=3,"background normalization forces square pixels"); ok("stream_loop" in (ROOT/"runtime/transform/process.py").read_text() and "loop_count" in resolver,"no-loop treatment is explicit")
    print(f"SELF_TEST_PASS checks={checks}"); print("contract_hash="+contract_hash())
if __name__=="__main__": main()
