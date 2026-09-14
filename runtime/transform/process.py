"""V1 caption/render entry point with one no-loop ordered background sequence."""
import os, sys
from base.contract import atomic_write_json, load_json
from resources.resolve import apply_concatenated_fit_to_short_treatment
from resources.validate import load_registry, asset_map
from transform import process_base as base
from transform.process_base import *

_caption_ass_focus_text=base._caption_ass_focus_text
_env_flag=base._env_flag; _word_highlight_enabled=base._word_highlight_enabled; _semantic_emphasis_enabled=base._semantic_emphasis_enabled
_dialogue=base._dialogue; _rapid_units=base._rapid_units; _semantic_local_punchline_indices=base._semantic_local_punchline_indices
_semantic_split_before=base._semantic_split_before; _semantic_metadata=base._semantic_metadata
_request_punchline_from_argv=base._request_punchline_from_argv
_BASE_CAPTURE=base.bounded_render_capture; _CURRENT_REQUEST_PATH=None

def _strip_loop(command):
    command=list(command)
    for i in range(len(command)-1):
        if command[i]=="-stream_loop" and command[i+1]=="-1": return command[:i]+command[i+2:]
    raise RuntimeError("no-loop compositor command is missing expected background loop marker")
def _duration(command):
    try:
        i=len(command)-1-list(reversed(command)).index("-t"); return float(command[i+1])
    except (ValueError,IndexError,TypeError): raise RuntimeError("cannot derive final render duration") from None
def _score(selection,registry):
    mapping=asset_map(registry); scores=[]
    sequence=selection.get("background_sequence")
    if not isinstance(sequence,list) or len(sequence)!=3: raise RuntimeError("selected V1 background sequence is missing")
    for z in sequence:
        bid=str(z.get("background_id") or ""); a=mapping.get(bid)
        if not a: raise RuntimeError(f"selected background asset is missing: {bid}")
        try: scores.append(float(a["caption_readability_score"]))
        except (KeyError,TypeError,ValueError): raise RuntimeError(f"selected background lacks caption readability: {bid}") from None
    return min(scores)
def continuous_render_capture(command):
    if not _CURRENT_REQUEST_PATH or "-stream_loop" not in command: return _BASE_CAPTURE(command)
    request=load_json(_CURRENT_REQUEST_PATH)
    if request.get("request_version")!=1: raise RuntimeError("Only V1 requests can render")
    final=_duration(command); selection_path=base.render.OUTPUT_DIR/"background_selection.json"; background=base.render.OUTPUT_DIR/"background.asset"
    selection=load_json(selection_path)
    if selection.get("background_treatment_pending") is not True: raise RuntimeError("V1 background treatment was not deferred correctly")
    test=os.getenv("STORY_TEST_MODE","").strip().lower() in {"1","true","yes","on"}
    metrics=apply_concatenated_fit_to_short_treatment(background,final,_score(selection,load_registry()),test_mode=test)
    selection["background_treatment_pending"]=False; selection["readability"]=metrics.pop("readability"); selection.setdefault("metrics",{}).update(metrics)
    atomic_write_json(selection_path,selection)
    return _BASE_CAPTURE(_strip_loop(command))
def main():
    global _CURRENT_REQUEST_PATH
    try: _CURRENT_REQUEST_PATH=sys.argv[sys.argv.index("--request")+1]
    except (ValueError,IndexError): _CURRENT_REQUEST_PATH=None
    base.bounded_render_capture=continuous_render_capture; base.main()
if __name__=="__main__": main()
