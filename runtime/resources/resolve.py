"""V1 background resolver facade."""
import argparse
from resources.fit import apply_concatenated_fit_to_short_treatment
from resources.sequence import resolve

def main():
    p=argparse.ArgumentParser(); p.add_argument("--request",required=True); p.add_argument("--registry"); p.add_argument("--no-download",action="store_true"); p.add_argument("--skip-preflight",action="store_true"); a=p.parse_args()
    try: resolve(a.request,a.registry,not a.no_download,not a.skip_preflight)
    except (ValueError,RuntimeError) as e: raise SystemExit(str(e)) from None
if __name__=="__main__": main()
