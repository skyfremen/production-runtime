"""Zodiac-only silent animated video renderer. No network, OAuth, Wacky imports or uploading.

The creative JSON remains owned by the PRIVATE planning repository. This module
draws procedural graphics and renders the complete mapping on all lookup/payoff
frames; it never truncates or skips an identity. Requires Pillow and ffmpeg.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from PIL import Image, ImageDraw, ImageFont
try:
    from .entrypoint import HandoffRejected, run as validate_input
except ImportError:
    from entrypoint import HandoffRejected, run as validate_input

W, H, FPS = 1080, 1920, 30
SAFE_L, SAFE_R = 82, 975
PALETTES = [
    ((14, 13, 36), (98, 65, 234), (245, 211, 121)),
    ((14, 35, 43), (26, 174, 157), (255, 200, 148)),
    ((41, 18, 40), (221, 88, 166), (254, 218, 169)),
]
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SCENE_KINDS = {"hook", "lookup", "payoff", "hold"}


def guard(condition, message):
    if not condition:
        raise HandoffRejected(message)


def font(size, bold=False):
    name = FONT_BOLD if bold else FONT_REGULAR
    guard(Path(name).is_file(), "required free DejaVu font missing")
    return ImageFont.truetype(name, size)


def measure(draw, value, f):
    l, t, r, b = draw.textbbox((0, 0), value, font=f, stroke_width=0)
    return (r - l, b - t)


def wrap(draw, value, f, max_width, max_lines):
    guard(isinstance(value, str) and bool(value.strip()), "blank creative text")
    lines = []
    for paragraph in value.splitlines():
        words = paragraph.strip().split()
        if not words:
            continue
        current = ""
        for word in words:
            guard(measure(draw, word, f)[0] <= max_width,
                  "unbreakable word exceeds mobile safe-area width")
            attempt = f"{current} {word}".strip()
            if measure(draw, attempt, f)[0] <= max_width:
                current = attempt
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
    guard(1 <= len(lines) <= max_lines, f"text does not fit {max_lines} readable lines")
    return lines


def draw_lines(d, text, x, y, width, size, max_lines, fill, bold=False, leading=1.36):
    f = font(size, bold)
    lines = wrap(d, text, f, width, max_lines)
    step = int(size * leading)
    for index, line in enumerate(lines):
        d.text((x, y + index * step), line, font=f, fill=fill, stroke_width=0)
    return y + len(lines) * step


def background(index, progress):
    bg, accent, glow = PALETTES[index % len(PALETTES)]
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((55, 164, 1015, 1775), radius=46, fill=tuple(min(255, c+10) for c in bg))
    d.rounded_rectangle((69, 178, 1001, 1760), radius=42, outline=accent, width=5)
    # Decorative orbit cycles smoothly; text and cards remain fixed and readable.
    phase = 2 * math.pi * progress
    cx = int(540 + 360 * math.sin(phase))
    cy = int(169 + 33 * math.cos(phase))
    d.ellipse((cx-27, cy-27, cx+27, cy+27), fill=glow)
    d.arc((200, 1522, 930, 1877), 190, 330, fill=accent, width=10)
    for k in range(9):
        ang = phase + k * (2*math.pi/9)
        xx = int(155 + 16 * math.cos(ang))
        yy = int(1745 + 11 * math.sin(ang))
        d.ellipse((xx-3, yy-3, xx+3, yy+3), fill=glow)
    return im


def scene_for_time(creative, t):
    for sc in creative["timed_scenes"]:
        if float(sc["start"]) <= t < float(sc["end"]):
            guard(sc.get("kind") in SCENE_KINDS, "unknown scene kind")
            return sc
    return creative["timed_scenes"][-1]


def compose(creative, t, idx, *, first_scene=False):
    duration = float(creative["duration_seconds"])
    scene = creative["timed_scenes"][0] if first_scene else scene_for_time(creative, t)
    kind = scene["kind"]
    img = background(idx, t / duration)
    d = ImageDraw.Draw(img)
    bg, accent, glow = PALETTES[idx % len(PALETTES)]
    white = (255, 252, 245)
    muted = (218, 219, 234)
    d.text((SAFE_L+12, 215), "WACKY ASTROLOGY  /  FOR FUN", font=font(28, True), fill=glow)
    # Headline is on frame zero, never preceded by logo/fade.
    draw_lines(d, creative["task_prompt"], SAFE_L+12, 273, 845,
               49, 3, white, True, 1.29)
    line_y = 486
    d.line((SAFE_L+15, line_y, SAFE_R-15, line_y), fill=accent, width=4)
    coverage = creative["target_identity_and_coverage"]
    identities = coverage["identities"]
    results = coverage["results"]
    lookup = kind != "hook"
    if lookup:
        # Two columns for a 12-result lookup, one column for small named subsets.
        cols = 2 if len(identities) >= 7 else 1
        rows = math.ceil(len(identities)/cols)
        cell_gap = 10
        top, bottom = 536, 1463
        col_width = (SAFE_R - SAFE_L - (cols-1)*cell_gap) // cols
        cell_height = (bottom - top - (rows-1)*cell_gap) // rows
        guard(cell_height >= (138 if cols == 2 else 105),
              "identity lookup too dense for safe, normal reading")
        # Each result remains visible throughout the entire lookup/payoff/hold.
        for n, identity in enumerate(identities):
            col, row = n//rows, n%rows
            x = SAFE_L + col*(col_width+cell_gap)
            y = top + row*(cell_height+cell_gap)
            d.rounded_rectangle((x, y, x+col_width, y+cell_height), radius=18,
                                fill=tuple(min(255, c+21) for c in bg), outline=accent, width=2)
            header_size = 31 if cols == 2 else 41
            result_size = 25 if cols == 2 else 34
            next_y = draw_lines(d, identity.upper(), x+19, y+11, col_width-38,
                                header_size, 1, glow, True, 1.1)
            value = results[identity]
            lines = wrap(d, value, font(result_size), col_width-38, 3)
            guard(next_y + len(lines)*int(result_size*1.27) + 10 <= y+cell_height,
                  f"identity {identity} result exceeds its visible cell")
            for line in lines:
                d.text((x+19, next_y+4), line, font=font(result_size), fill=white)
                next_y += int(result_size*1.27)
        subtitle = "YOUR RESULT  +  SOMEONE ELSE'S" if kind=="lookup" else "THE COMPLETE REVEAL"
        d.text((SAFE_L+16, 1513), subtitle, font=font(29, True), fill=glow)
    else:
        d.rounded_rectangle((112, 605, 941, 1410), radius=70, fill=tuple(min(255,c+24) for c in bg),
                            outline=accent, width=6)
        # Distinct visual object in the first frame: portal plus personal lookup task.
        d.ellipse((288, 711, 767, 1190), outline=glow, width=20)
        d.ellipse((358, 784, 699, 1123), outline=accent, width=24)
        d.polygon([(522, 838), (600, 955), (522, 1087), (444, 955)], fill=glow)
        d.text((289, 1226), "LOOK FOR YOUR RESULT", font=font(33, True), fill=white)
        d.text((210, 1515), "ONE FIRST WATCH = A COMPLETE ANSWER", font=font(26, True), fill=muted)
    if lookup:
        first = creative["first_view_payoff"]
        # Not a cut-off teaser. The source plan must fit one complete payoff.
        draw_lines(d, first, SAFE_L+14, 1563, SAFE_R-SAFE_L-45,
                   29, 3, white, True, 1.25)
    d.text((SAFE_L+15, 1712), "FIND YOURS  /  CHECK THEIRS", font=font(25, True), fill=muted)
    # Enforce no painted text below 1750 (past phone-safe video controls).
    return img


def check_render_prerequisites(creative):
    guard(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe required")
    for s in creative["timed_scenes"]:
        guard(s["kind"] in SCENE_KINDS, "unsupported scene kind")
    guard(creative["timed_scenes"][-1]["kind"] == "hold", "no final full-answer hold")
    guard(creative["timed_scenes"][0]["kind"] == "hook", "no immediate first-frame hook")
    guard(creative["target_identity_and_coverage"]["results"],
          "missing answers")
    guard(creative["duration_seconds"] * FPS <= 3600, "duration safety cap")
    # Dry-run full visual fit on hook and all reveal types before opening ffmpeg.
    for k in ("hook", "lookup", "payoff", "hold"):
        matches = [x for x in creative["timed_scenes"] if x["kind"] == k]
        guard(bool(matches), "required scene missing: "+k)
        compose(creative, (float(matches[0]["start"])+float(matches[0]["end"]))/2, 0)


def render_one(creative, output, palette_index=0):
    check_render_prerequisites(creative)
    output = Path(output)
    guard(re.fullmatch(r"za-[a-z0-9-]{8,64}", creative["concept_id"]) is not None,
          "invalid isolated concept ID")
    duration = float(creative["duration_seconds"])
    frames = round(duration*FPS)
    guard(frames >= 90 and abs(frames/FPS-duration) <= 1/FPS,
          "duration not representable at 30 fps")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(".part.mp4")
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
            "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)]
    first = compose(creative, 0, palette_index, first_scene=True)
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for n in range(frames):
            t = n/FPS
            frame = compose(creative, t, palette_index)
            # Only the final 0.2s reset to the already shown first-frame hook.
            # Final 0.75+s hold remains fully readable until this seam.
            seam = 0.2
            if t >= duration-seam:
                alpha = (t-(duration-seam))/seam
                frame = Image.blend(frame, first, max(0, min(1, alpha)))
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        err = proc.stderr.read()
        code = proc.wait(timeout=120)
        guard(code == 0, "ffmpeg render failed: " + err.decode("utf8", "replace")[-500:])
        guard(tmp.exists() and tmp.stat().st_size > 10000, "empty/corrupt MP4")
        tmp.replace(output)
    except BaseException:
        try: proc.kill()
        except OSError: pass
        try: proc.stdin.close()
        except (OSError, ValueError): pass
        try: proc.wait(timeout=5)
        except BaseException: pass
        tmp.unlink(missing_ok=True)
        raise
    return {"path": str(output), "frames": frames, "duration_seconds": frames/FPS}


if __name__ == "__main__":
    raise SystemExit("Use runtime/zodiac/artifacts.py; no standalone upload path")
