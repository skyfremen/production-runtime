"""Small deterministic checks for the caption-safe region of a selected clip."""

import re
import subprocess

SAMPLE_COUNT = 12
MIN_REGISTRY_READABILITY = 60
MAX_PROTECTION_ALPHA = 0.38
SAFE_REGION = {"x": 0.12, "y": 0.34, "width": 0.76, "height": 0.34}


def protection_for_samples(yavg, yhigh, ydiff):
    peak = max(yhigh or [0.0])
    average = sum(yavg) / max(1, len(yavg))
    change = max(ydiff or [0.0])
    alpha = 0.18
    if peak >= 220 or average >= 175:
        alpha = 0.30
    if peak >= 242 or average >= 205 or change >= 32:
        alpha = MAX_PROTECTION_ALPHA
    return round(alpha, 2)


def analyze_caption_region(path, duration_seconds, registry_score):
    """Sample the used segment over time and return a bounded protection decision."""
    if float(registry_score or 0) < MIN_REGISTRY_READABILITY:
        raise RuntimeError("caption-safe region failed the registered readability floor")
    duration = max(1.0, float(duration_seconds or 1.0))
    sample_rate = SAMPLE_COUNT / duration
    r = SAFE_REGION
    vf = (
        f"crop=iw*{r['width']}:ih*{r['height']}:iw*{r['x']}:ih*{r['y']},"
        f"fps={sample_rate:.8f},scale=180:-2,signalstats,metadata=print"
    )
    process = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-stream_loop", "-1",
         "-t", f"{duration:.3f}", "-i", str(path), "-an", "-vf", vf,
         "-frames:v", str(SAMPLE_COUNT),
         "-f", "null", "-"], capture_output=True, text=True,
    )
    if process.returncode != 0:
        raise RuntimeError("caption-safe region sampling failed")
    values = {"YAVG": [], "YHIGH": [], "YDIF": []}
    for key, raw in re.findall(r"lavfi\.signalstats\.(YAVG|YHIGH|YDIF)=([0-9.]+)", process.stderr):
        values[key].append(float(raw))
    if not values["YAVG"] or not values["YHIGH"]:
        raise RuntimeError("caption-safe region sampling produced no measurements")
    alpha = protection_for_samples(values["YAVG"], values["YHIGH"], values["YDIF"])
    return {
        "sample_count": len(values["YAVG"]), "safe_region": r,
        "mean_luma": round(sum(values["YAVG"]) / len(values["YAVG"]), 3),
        "peak_luma": round(max(values["YHIGH"]), 3),
        "max_temporal_luma_difference": round(max(values["YDIF"] or [0.0]), 3),
        "registry_score": float(registry_score),
        "protection": "soft_caption_band", "protection_alpha": alpha,
        "protection_applied": alpha > 0, "passed": True,
    }
