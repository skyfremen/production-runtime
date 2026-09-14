"""Offline V1 contract checks. No private-state or YouTube network access."""
import copy
from pathlib import Path
from base.compat import CONTRACT, contract_hash, validate_contract_hash
from guard.schema import validate_request_data
from output.state import evidence_path, result_path
from output.transfer import build_upload_body
from transport import EXEC_PREFIX, REQUEST_PREFIX, REGISTRY_PATH
ROOT=Path(__file__).resolve().parents[1]; HASH="a40b144e0098c26b2bf578cc8fbebe018a798d3cfbc7399f4e9668a857f01314"; CID="wd-0123456789abcdef01234567"
REQ={"request_version":1,"content_id":CID,"source_draft_id":"draft-20260914-deadbeef","channel":{"name":"Wacky Dramas","handle":"@WACKYDRAMAS"},
"story":{"category":"work","premise":"A false accusation","conflict":"A manager blames her","twist":"The logs prove otherwise","hook":"My manager blamed me.","script":"I stayed quiet until I had the receipts. Then the audit log showed exactly who changed it.","lead_gender":"female","story_tone":"dramatic","punchline":"I had the receipts","card_emojis":["😳","💬","🔥","👀"]},
"narration":{"engine":"kokoro","voice":"af_bella","speed":1.75},"background":{"mode":"concatenated_fit_to_short","segments":[{"background_id":x,"segment_start_seconds":0.0,"segment_duration_seconds":60.0} for x in "abc"]},
"youtube":{"title":"The Audit Log Changed Everything","description":"A workplace accusation flips fast.","hashtags":["#WackyDramas","#Shorts"],"tags":["Wacky Dramas","Shorts"],"category_id":"24","made_for_kids":False},
"visibility":"public","render":{"width":1080,"height":1920,"fps":30,"video_codec":"h264","h264_profile":"high","pixel_format":"yuv420p","audio_codec":"aac","audio_sample_rate":48000,"background_music":False}}
def ok(name,v):
    if not v: raise AssertionError(name)
    print("PASS",name)
def main():
    validate_request_data(REQ); ok("v1 request schema",1)
    ok("single compatibility hash",contract_hash()==HASH and validate_contract_hash(HASH)==HASH)
    try: validate_contract_hash("0"*64); mismatch=False
    except ValueError: mismatch=True
    ok("compatibility mismatch fails",mismatch)
    body=build_upload_body(REQ); ok("immediate PUBLIC body",body["status"]["privacyStatus"]=="public" and "publishAt" not in body["status"])
    bad=copy.deepcopy(REQ); bad["publish_at"]="2099-01-01T00:00:00Z"
    try: validate_request_data(bad); rejected=False
    except ValueError: rejected=True
    ok("scheduling fields rejected",rejected)
    ok("new private paths",(EXEC_PREFIX,REQUEST_PREFIX,REGISTRY_PATH)==("content/executions/","content/requests/","data/backgrounds.json"))
    ok("durable evidence path",evidence_path(CID,"intent")==f"content/executions/evidence/{CID}/intent.json")
    ok("immutable result path",result_path(CID)==f"content/results/{CID}.json")
    ok("contract descriptor",CONTRACT["visibility"]=="public" and CONTRACT["request_version"]==CONTRACT["execution_version"]==CONTRACT["result_version"]==1)
    single=(ROOT/".github/workflows/single.yml").read_text(); ok("opaque dispatch inputs",all(x in single for x in ("execution_id","source_sha","contract_hash","dispatch_id")))
    ok("no full request workflow input","narration" not in single and "title" not in single)
    texts="\n".join(p.read_text(errors="ignore") for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and p.suffix in {".py",".yml",".yaml",".md",".json"})
    ok("no legacy private path references",("youtube-"+"shorts-"+"bot/") not in texts)
    workflows={p.name for p in (ROOT/".github/workflows").glob("*.yml")}; ok("no Daily/analytics runtime workflows",not ({"run.yml","observe.yml","review-evidence.yml","dry-run.yml"}&workflows))
    ok("no legacy contract hashes","LEGACY_CONTRACT_HASH" not in (ROOT/"runtime/base/compat.py").read_text())
    transfer=(ROOT/"runtime/output/transfer.py").read_text(); ok("duplicate upload fence",'record_type":"intent' in transfer); ok("ambiguous intent blocks","duplicate upload forbidden" in transfer)
    execute=(ROOT/"runtime/output/execute.py").read_text(); ok("intent not created during render preparation","prepare_upload" in execute and "upload_new" in execute)
    result=(ROOT/"runtime/output/result.py").read_text(); ok("result is verified-public only","verified" in result and "public" in result)
    print("SELF_TEST_PASS checks=18 contract_hash="+contract_hash())
if __name__=="__main__": main()
