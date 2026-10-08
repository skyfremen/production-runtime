"""Render and technically inspect *private* Zodiac handoffs into an artifact directory.

Local-only file processor. It never calls an upload API, and invokes only
ffmpeg/ffprobe. The private GitHub Actions caller owns artifact delivery.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from PIL import Image, ImageStat
try:
    from .entrypoint import run as validate_input, HandoffRejected
    from .renderer import render_one, W, H, FPS
except ImportError:
    from entrypoint import run as validate_input, HandoffRejected
    from renderer import render_one, W, H, FPS


def check(condition, message):
    if not condition:
        raise HandoffRejected(message)


def command(args, timeout=120):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    check(result.returncode == 0, "media verification failed: "+result.stderr[-650:])
    return result


def probe(path, expected_duration):
    raw = command(["ffprobe", "-v", "error", "-show_entries",
                   "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,pix_fmt,nb_frames",
                   "-of", "json", str(path)], timeout=60).stdout
    data = json.loads(raw)
    streams = data.get("streams", [])
    check(len(streams) == 1 and streams[0].get("codec_type") == "video",
          "video must have exactly one stream and no audio")
    v = streams[0]
    check(v.get("codec_name") == "h264" and
          v.get("pix_fmt") == "yuv420p" and
          (v.get("width"), v.get("height")) == (W, H) and
          v.get("avg_frame_rate") == "30/1", "wrong codec/resolution/framerate")
    length = float(data["format"]["duration"])
    check(abs(length-expected_duration) <= 1/FPS+0.03,
          "encoded duration diverges from timed creative")
    number = v.get("nb_frames")
    check(number is not None and int(number) == round(expected_duration*FPS),
          "video frame count mismatch")
    check(Path(path).stat().st_size > 10000, "MP4 too small")
    return {"width": v["width"], "height": v["height"], "fps": FPS,
            "codec": v["codec_name"], "pix_fmt": v["pix_fmt"],
            "audio_streams": 0, "duration_seconds": length,
            "frame_count": int(number), "size_bytes": Path(path).stat().st_size}


def inspect_black_and_decode(path):
    command(["ffmpeg", "-v", "error", "-xerror", "-i", str(path),
             "-f", "null", "-"], timeout=180)
    p = command(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                 "-vf", "blackdetect=d=0.10:pix_th=0.08",
                 "-an", "-f", "null", "-"], timeout=180)
    check("black_start:" not in p.stderr, "black/blank frames detected")


def capture(path, when, target):
    # Screenshots are extracted from the *encoded* MP4, not the PIL drawing.
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    command(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(when),
             "-i", str(path), "-frames:v", "1", str(target)], timeout=60)
    check(target.is_file() and target.stat().st_size > 5000,
          "encoded preview screenshot missing")
    with Image.open(target) as im:
        check(im.size == (W, H), "screenshot dimension mismatch")
        # Contrast and luminance are conservative sanity gates, not OCR.
        low = ImageStat.Stat(im.convert("L")).mean[0]
        check(8.0 <= low <= 235, "frame is too dark or washed out")
        probe = im.crop((90, 550, 940, 1460)).convert("L")
        stats = ImageStat.Stat(probe)
        check(stats.extrema[0][1] - stats.extrema[0][0] > 45,
              "center card scene lacks adequate contrast")
        return {"mean_luma": round(low, 2), "contrast_range":
                stats.extrema[0][1]-stats.extrema[0][0]}


def create_artifact(validation, folder):
    items = validation["validated_requests"]
    check(validation["mode"] == "validation_only" and
          not validation["upload_enabled"] and not validation["render_enabled"] and
          not validation["ready_for_render"], "Zodiac plan boundaries changed")
    folder = Path(folder)
    check(not folder.exists(), "artifact directory already exists (no overwrite)")
    folder.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="zodiac-render-", dir=folder.parent))
    manifest = {"schema_version": 1, "lane": "zodiac",
                "delivery": "github_actions_artifact_only",
                "source_repository": validation["source_repository"],
                "source_revision": validation["source_revision"],
                "handoff_sha256": validation["handoff_sha256"],
                "youtube_upload_enabled": False, "videos": []}
    report = {"schema_version": 1, "passed": False, "checks":
              ["strict Zodiac handoff", "text-fit/layout at fixed safe area",
               "every promised identity visible", "muted 30fps H264",
               "full decode", "blackdetect", "sampled encoded-frame contrast",
               "recorded content checksums"],
              "concepts": []}
    try:
        check(1 <= len(items) <= 10, "bad number of approved concepts")
        for idx, item in enumerate(items):
            creative = item["editorial"]
            cid = item["concept_id"]
            path = tmp/"videos"/(cid+".mp4")
            result = render_one(creative, path, idx)
            md = probe(path, result["duration_seconds"])
            inspect_black_and_decode(path)
            frames = {}
            scenes = creative["timed_scenes"]
            for kind in ("hook", "lookup", "payoff", "hold"):
                scene = next(s for s in scenes if s["kind"] == kind)
                at = (scene["start"]+scene["end"])/2
                if kind == "hold":
                    at = max(scene["start"]+.1, scene["end"]-.32)
                frames[kind] = capture(path, at, tmp/"previews"/f"{cid}-{kind}.png")
            digest = sha256(path.read_bytes()).hexdigest()
            manifest["videos"].append({"concept_id": cid, "file": f"videos/{cid}.mp4",
                                        "sha256": digest, **md})
            report["concepts"].append({
                "concept_id": cid, "status": "technical_qc_pass",
                "sampling": frames, "readability_contract":
                "text bounds/wrapping mechanically checked; human review still required",
                "identity_count": len(creative["target_identity_and_coverage"]["identities"]),
                "first_view_payoff": creative["first_view_payoff"],
                "loop_review": "0.2s reset crossfade; human visual approval outstanding",
            })
        check(len(manifest["videos"]) == len(items), "partial artifact refused")
        report["passed"] = True
        (tmp/"manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+"\n", encoding="utf8")
        (tmp/"qc-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n", encoding="utf8")
        tmp.rename(folder)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"artifact_dir": str(folder), "videos": len(manifest["videos"]),
            "qc_passed": True, "youtube_upload_enabled": False}


def main(argv=None):
    p=argparse.ArgumentParser(description="Zodiac private artifact renderer (no YouTube)")
    p.add_argument("--handoff", required=True)
    p.add_argument("--output", required=True)
    args=p.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="zodiac-validate-") as td:
            validation=validate_input(args.handoff, Path(td)/"validation.json")
        status=create_artifact(validation, args.output)
        print(json.dumps(status, sort_keys=True))
        return 0
    except (HandoffRejected, OSError, ValueError, KeyError, TypeError,
            subprocess.TimeoutExpired) as err:
        print(f"ZODIAC_ARTIFACT_REJECTED: {err}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
