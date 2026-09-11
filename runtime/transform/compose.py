import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFile, ImageFont, ImageOps, ImageStat

from transform.synth import (
    ONNX_BACKEND,
    PYTORCH_FALLBACK_BACKEND,
    OnnxKokoroSynthesizer,
    PytorchKokoroSynthesizer,
    audio_metrics,
)
from base.contract import (
    END_TAIL_SECONDS, START_LEAD_SECONDS, PRODUCTION_ENCODE_SAFETY_SECONDS, OUTPUT_DIR,
    PRODUCTION_MAX_SECONDS, atomic_write_json,
    env_bool, expected_video_config, load_json,
)

ImageFile.LOAD_TRUNCATED_IMAGES = True
BASE = Path(__file__).resolve().parents[1]
ASSETS = BASE / "assets"
UI_ASSETS = ASSETS / "ui"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
EMOJI_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
]
CARD_TRANSITION_SECONDS = 0.30

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FFMPEG_THREADS = max(0, int(os.getenv("FFMPEG_THREADS", "0")))

CAPTION_MARGIN_X = 128
CAPTION_MAX_WIDTH = VIDEO_WIDTH - (2 * CAPTION_MARGIN_X)
CAPTION_FONT_SIZE = 78
CAPTION_MIN_FONT_SIZE = 59
CAPTION_MAX_LINES = 3
CAPTION_OUTLINE = 8
CAPTION_SHADOW = 2

CARD_BOX = (56, 236, 1024, 806)
CARD_RADIUS = 36
CARD_SHADOWS = ((11, 55), (18, 25))
CARD_BORDER_WIDTH = 5
AVATAR_SIZE = 132
AVATAR_POS = (83, 290)
CHANNEL_NAME_POS = (236, 285)
CHANNEL_NAME_FONT_SIZE = 41
VERIFIED_ICON_SIZE = 38
VERIFIED_ICON_GAP = 18
VERIFIED_ICON_Y_OFFSET = 5
EMOJI_ROW_Y = 350
EMOJI_STEP_X = 77
EMOJI_TARGET_SIZE = 66
EMOJI_CELL_SIZE = 59
HOOK_POS = (86, 455)
HOOK_MAX_WIDTH = 900
HOOK_MAX_HEIGHT = 221
HOOK_MAX_FONT_SIZE = 65
HOOK_MIN_FONT_SIZE = 41
HOOK_LINE_GAP = 9
FOOTER_Y = 743
FOOTER_FONT_SIZE = 30
LIKE_ICON_SIZE = 30
COMMENT_ICON_SIZE = 32
SHARE_ICON_SIZE = 29
LIKE_X = 83
COMMENT_X = 215
SHARE_X = 869
FOOTER_TEXT_GAP = 11
HANDLE_PILL = (285, 1260, 795, 1334)
SUBSCRIBE_PILL = (360, 1349, 720, 1418)
HANDLE_FONT_SIZE = 42
SUBSCRIBE_FONT_SIZE = 38
PILL_OUTLINE_WIDTH = 2
CARD_BOB_AMPLITUDE = 6
X264_PRESET = "superfast"
X264_CRF = 19
BLACKDETECT_FILTER = "blackdetect=d=0.50:pic_th=0.98:pix_th=0.10"
BLACKDETECT_MAX_ALLOWED_SECONDS = 0.75


def run_capture(cmd):
    started = time.monotonic()
    process = subprocess.run(cmd, capture_output=True, text=True)
    if process.stdout:
        print(process.stdout, end="")
    if process.stderr:
        print(process.stderr, end="", file=os.sys.stderr)
    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode, cmd, output=process.stdout, stderr=process.stderr
        )
    return round(time.monotonic() - started, 6), process.stderr or ""


def ass_time(seconds):
    cs = int(round(max(0.0, seconds) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def escape_ass(text):
    return str(text).replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def phrase_chunks(text, max_words=3):
    words = text.split()
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]


def sentence_prefixes(text, max_words=42):
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x.strip()]
    prefixes = []
    words = []
    for sentence in sentences:
        sw = sentence.split()
        if words and len(words) + len(sw) > max_words:
            break
        words.extend(sw)
        prefixes.append(" ".join(words))
        if len(words) >= max_words:
            break
    if not prefixes:
        words = text.split()[:max_words]
        if words:
            prefixes = [" ".join(words)]
    return prefixes


def synthesize(synthesizer, text, voice, speed):
    audio, segments, _metrics = synthesizer.synthesize(text, voice, speed)
    return audio, segments


def choose_test_narration(synthesizer, script, voice, speed, max_seconds):
    prefixes = sentence_prefixes(script, max_words=42)
    target_low = max(1.5, max_seconds - 0.8)
    best = None
    for excerpt in prefixes:
        audio, segments = synthesize(synthesizer, excerpt, voice, speed)
        duration = len(audio) / 24000.0
        best = (excerpt, audio, segments, duration)
        if duration >= target_low:
            break
    if best is None:
        raise RuntimeError("Could not derive a test excerpt")
    excerpt, audio, segments, duration = best
    if duration <= max_seconds:
        return excerpt, audio, segments

    words = excerpt.split()
    keep = max(3, int(len(words) * (max_seconds - 0.15) / duration))
    shorter = " ".join(words[:keep]).rstrip(",;:-")
    audio, segments = synthesize(synthesizer, shorter, voice, speed)
    duration = len(audio) / 24000.0
    if duration > max_seconds:
        keep = max(3, int(keep * (max_seconds - 0.15) / duration))
        shorter = " ".join(words[:keep]).rstrip(",;:-")
        audio, segments = synthesize(synthesizer, shorter, voice, speed)
    return shorter, audio, segments


def generate_narration(synthesizer, hook, story_text, voice, speed, test_mode, test_max):
    started = time.monotonic()
    intro_audio, _intro_segments = synthesize(synthesizer, hook, voice, speed)
    intro_duration = len(intro_audio) / 24000.0
    story_budget = test_max - START_LEAD_SECONDS - intro_duration - CARD_TRANSITION_SECONDS if test_mode else None
    if test_mode:
        if story_budget < 1.0:
            raise RuntimeError(
                f"Opening card narration ({intro_duration:.3f}s) leaves too little room for a story excerpt "
                f"inside STORY_RENDER_MAX_SECONDS={test_max:.3f}s; shorten story.hook for testability."
            )
        narration_text, story_audio, tts_segments = choose_test_narration(
            synthesizer, story_text, voice, speed, story_budget
        )
    else:
        narration_text = story_text
        story_audio, tts_segments = synthesize(synthesizer, story_text, voice, speed)
    return {
        "narration_text": narration_text,
        "intro_audio": intro_audio,
        "story_audio": story_audio,
        "tts_segments": tts_segments,
        "intro_duration": intro_duration,
        "tts_generation_duration_seconds": round(time.monotonic() - started, 6),
    }



def font_px(path, size):
    return ImageFont.truetype(path, max(12, int(size)))


def wrap_pixels(draw, text, fnt, max_width, max_lines=3):
    lines, current = [], ""
    for word in str(text).split():
        trial = f"{current} {word}".strip()
        width = draw.textbbox((0, 0), trial, font=fnt)[2]
        if current and width > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines[:max_lines], len(lines) <= max_lines


def fit_hook(draw, text, max_width, max_height):
    for font_size in range(HOOK_MAX_FONT_SIZE, HOOK_MIN_FONT_SIZE - 1, -1):
        fnt = font_px(FONT_BOLD, font_size)
        lines, fits = wrap_pixels(draw, text, fnt, max_width, 6)
        bb = draw.textbbox((0, 0), "Ag", font=fnt)
        line_height = (bb[3] - bb[1]) + HOOK_LINE_GAP
        widths_fit = all(draw.textlength(line, font=fnt) <= max_width for line in lines)
        if fits and widths_fit and len(lines) * line_height <= max_height:
            return fnt, lines, line_height
    raise ValueError("Complete opening hook cannot fit card bounds; refusing to truncate text")


def caption_layout(text):
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    for font_size in range(CAPTION_FONT_SIZE, CAPTION_MIN_FONT_SIZE - 1, -1):
        fnt = font_px(FONT_BOLD, font_size)
        lines, current = [], ""
        oversized = False
        for word in str(text).split():
            if draw.textlength(word, font=fnt) > CAPTION_MAX_WIDTH:
                oversized = True
                break
            trial = (current + " " + word).strip()
            if current and draw.textlength(trial, font=fnt) > CAPTION_MAX_WIDTH:
                lines.append(current)
                current = word
            else:
                current = trial
        if oversized:
            continue
        if current:
            lines.append(current)
        if len(lines) <= CAPTION_MAX_LINES:
            return "\n".join(lines), font_size
    raise ValueError("Caption cannot fit safe central region without clipping")


def wrap_caption(text):
    return caption_layout(text)[0]


def caption_ass_text(text):
    wrapped, event_font_size = caption_layout(text)
    payload = escape_ass(wrapped)
    if event_font_size != CAPTION_FONT_SIZE:
        return f"{{\fs{event_font_size}}}{payload}"
    return payload


def build_ass_header():
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_WIDTH}
PlayResY: {VIDEO_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Main,DejaVu Sans,{CAPTION_FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00101010,&H35000000,-1,0,0,0,100,100,0,0,1,{CAPTION_OUTLINE},{CAPTION_SHADOW},5,{CAPTION_MARGIN_X},{CAPTION_MARGIN_X},0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""


def centered_text(draw, box, text, fnt, fill):
    x1, y1, x2, y2 = box
    bb = draw.textbbox((0, 0), text, font=fnt)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    draw.text((x1 + (x2 - x1 - tw) / 2, y1 + (y2 - y1 - th) / 2 - 2), text, font=fnt, fill=fill)


def load_ui_icon(name, target_size):
    path = UI_ASSETS / name
    if not path.exists():
        raise RuntimeError(f"Missing required UI asset: {path}")
    with Image.open(path) as source:
        source.load()
        icon = source.convert("RGBA").copy()
    bbox = icon.getchannel("A").getbbox()
    if not bbox:
        raise RuntimeError(f"UI asset has no visible pixels: {path}")
    icon = icon.crop(bbox)
    return ImageOps.contain(icon, (target_size, target_size), method=Image.Resampling.LANCZOS)


def paste_icon_centered(canvas, icon, x, center_y):
    y = int(round(center_y - icon.height / 2))
    canvas.alpha_composite(icon, (int(x), y))
    return int(x + icon.width)


def draw_text_centered_y(draw, x, center_y, text, fnt, fill):
    bb = draw.textbbox((0, 0), text, font=fnt)
    y = center_y - (bb[3] - bb[1]) / 2 - bb[1]
    draw.text((x, y), text, font=fnt, fill=fill)
    return bb[2] - bb[0]


def render_emoji(icon, target_size):
    for emoji_font in EMOJI_FONT_CANDIDATES:
        if not Path(emoji_font).exists():
            continue
        try:
            tile_size = 150
            tile = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(tile)
            preferred_size = 109 if "ColorEmoji" in emoji_font else 96
            fnt = ImageFont.truetype(emoji_font, preferred_size)
            bb = draw.textbbox((0, 0), icon, font=fnt, embedded_color=True)
            x = (tile_size - (bb[2] - bb[0])) / 2 - bb[0]
            y = (tile_size - (bb[3] - bb[1])) / 2 - bb[1]
            draw.text((x, y), icon, font=fnt, embedded_color=True)
            bbox = tile.getbbox()
            if bbox:
                tile = tile.crop(bbox)
                return ImageOps.contain(tile, (target_size, target_size), method=Image.Resampling.LANCZOS)
        except (OSError, ValueError):
            continue
    raise RuntimeError(f"Unable to render requested card emoji: {icon}")


def caption_events(text, tts_segments, speech_duration, start_offset=0.0):
    events = []
    usable = [(t, n) for t, n in tts_segments if t and n > 0]
    segment_words = sum(len(t.split()) for t, _ in usable)
    text_words = len(text.split())
    use_segments = bool(usable) and segment_words >= max(1, int(text_words * 0.75))
    cursor = 0.0
    if use_segments:
        for seg_text, samples in usable:
            seg_start = cursor
            seg_duration = min(samples / 24000.0, max(0.0, speech_duration - seg_start))
            if seg_duration <= 0:
                break
            chunks = phrase_chunks(seg_text, 3)
            weights = [max(1, sum(len(w.strip(".,!?;:\"()[]{}")) for w in c.split())) for c in chunks]
            total = max(1, sum(weights))
            local = seg_start
            for idx, (chunk, weight) in enumerate(zip(chunks, weights)):
                end = min(speech_duration, seg_start + seg_duration if idx == len(chunks) - 1 else local + seg_duration * weight / total)
                if end > local + 0.03:
                    events.append(f"Dialogue: 0,{ass_time(start_offset + local)},{ass_time(start_offset + end)},Main,,0,0,0,,{caption_ass_text(chunk.upper())}")
                local = end
            cursor = min(speech_duration, seg_start + seg_duration)
    else:
        chunks = phrase_chunks(text, 3)
        weights = [max(1, sum(len(w.strip(".,!?;:\"()[]{}")) for w in c.split())) for c in chunks]
        total = max(1, sum(weights))
        for idx, (chunk, weight) in enumerate(zip(chunks, weights)):
            end = speech_duration if idx == len(chunks) - 1 else min(speech_duration, cursor + speech_duration * weight / total)
            if end > cursor + 0.03:
                events.append(f"Dialogue: 0,{ass_time(start_offset + cursor)},{ass_time(start_offset + end)},Main,,0,0,0,,{caption_ass_text(chunk.upper())}")
            cursor = end
    return events


def main():
    render_started_at = datetime.now(timezone.utc)
    render_timer = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request = load_json(args.request)
    selection_path = OUTPUT_DIR / "background_selection.json"
    background = OUTPUT_DIR / "background.asset"
    if not selection_path.exists() or not background.exists():
        raise SystemExit("Render requires resolved output/background_selection.json and background.asset")
    selection = load_json(selection_path)

    cfg = expected_video_config()
    fps = cfg["fps"]
    if (cfg["width"], cfg["height"]) != (VIDEO_WIDTH, VIDEO_HEIGHT):
        raise SystemExit("Transformation requires fixed 1080x1920 dimensions")
    if fps <= 0:
        raise SystemExit("VIDEO_FPS must be positive")
    W, H = VIDEO_WIDTH, VIDEO_HEIGHT

    story = request["story"]
    narration_cfg = request["narration"]
    script = str(story["script"]).strip()
    hook = str(story["hook"]).strip()
    channel_name = request["channel"]["name"]
    handle = request["channel"]["handle"]
    voice = narration_cfg["voice"]
    speed = float(narration_cfg["speed"])
    test_mode = env_bool("STORY_TEST_MODE", False)
    test_max = float(os.getenv("STORY_RENDER_MAX_SECONDS", "5"))
    if test_mode and not 1.0 <= test_max <= 15.0:
        raise SystemExit("STORY_RENDER_MAX_SECONDS must be 1-15 seconds in test mode")

    import soundfile as sf

    story_text = str(script).strip()
    if not story_text:
        raise SystemExit("story.script must contain story narration")

    narration_backend_requested = ONNX_BACKEND
    narration_backend_used = ONNX_BACKEND
    narration_fallback_used = False
    narration_fallback_reason = None
    onnx_init_duration_seconds = 0.0
    pytorch_fallback_init_duration_seconds = 0.0

    onnx_init_started = time.monotonic()
    try:
        synthesizer = OnnxKokoroSynthesizer()
        onnx_init_duration_seconds = synthesizer.init_seconds
        generated = generate_narration(
            synthesizer, hook, story_text, voice, speed, test_mode, test_max
        )
    except Exception as exc:
        onnx_init_duration_seconds = round(time.monotonic() - onnx_init_started, 6)
        narration_backend_used = PYTORCH_FALLBACK_BACKEND
        narration_fallback_used = True
        narration_fallback_reason = f"{type(exc).__name__}: {exc}"[:500]
        print(f"::warning::Kokoro ONNX FP32 failed; using PyTorch fallback: {narration_fallback_reason}", file=os.sys.stderr)
        fallback = PytorchKokoroSynthesizer()
        pytorch_fallback_init_duration_seconds = fallback.init_seconds
        generated = generate_narration(
            fallback, hook, story_text, voice, speed, test_mode, test_max
        )

    narration_text = generated["narration_text"]
    intro_audio = generated["intro_audio"]
    story_audio = generated["story_audio"]
    tts_segments = generated["tts_segments"]
    intro_duration = generated["intro_duration"]
    tts_generation_duration_seconds = generated["tts_generation_duration_seconds"]

    story_duration = len(story_audio) / 24000.0
    lead_samples = int(round(START_LEAD_SECONDS * 24000))
    lead_audio = np.zeros(max(1, lead_samples), dtype=np.float32)
    transition_samples = int(round(CARD_TRANSITION_SECONDS * 24000))
    transition_audio = np.zeros(max(1, transition_samples), dtype=np.float32)
    speech_audio = np.concatenate([lead_audio, intro_audio, transition_audio, story_audio])
    story_start = START_LEAD_SECONDS + intro_duration + CARD_TRANSITION_SECONDS
    speech_duration = len(speech_audio) / 24000.0
    final_duration = speech_duration + END_TAIL_SECONDS
    if test_mode:
        if speech_duration > test_max + 0.05:
            raise SystemExit(f"Test narration {speech_duration:.3f}s exceeds STORY_RENDER_MAX_SECONDS={test_max:.3f}s")
    elif final_duration > PRODUCTION_MAX_SECONDS - PRODUCTION_ENCODE_SAFETY_SECONDS:
        raise SystemExit(
            f"Production duration {final_duration:.3f}s exceeds safe render limit "
            f"{PRODUCTION_MAX_SECONDS - PRODUCTION_ENCODE_SAFETY_SECONDS:.2f}s for the hard {PRODUCTION_MAX_SECONDS:.0f}s ceiling. "
            "Do not trim; create a shorter story with a new content_id."
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    narration = OUTPUT_DIR / "narration.wav"
    sf.write(narration, speech_audio, 24000)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    brand = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dc, db = ImageDraw.Draw(card), ImageDraw.Draw(brand)

    box = CARD_BOX
    for offset, alpha in CARD_SHADOWS:
        shadow = (box[0] + offset, box[1] + offset, box[2] + offset, box[3] + offset)
        dc.rounded_rectangle(shadow, radius=CARD_RADIUS, fill=(0, 0, 0, alpha))
    dc.rounded_rectangle(
        box,
        radius=CARD_RADIUS,
        fill=(251, 251, 251, 252),
        outline=(28, 28, 28, 255),
        width=CARD_BORDER_WIDTH,
    )

    logo_path = ASSETS / "identity.png"
    if not logo_path.exists():
        raise SystemExit(f"Missing required asset: {logo_path}")
    with Image.open(logo_path) as src:
        src.load()
        logo = src.convert("RGBA").copy()
    rgb = logo.convert("RGB")
    stat = ImageStat.Stat(rgb)
    extrema = rgb.getextrema()
    if max(stat.mean) < 18 or sum(hi - lo for lo, hi in extrema) < 90:
        raise SystemExit("identity resource appears blank or corrupt")
    avatar = logo.resize((AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
    mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, AVATAR_SIZE - 1, AVATAR_SIZE - 1), fill=255)
    clipped = Image.new("RGBA", (AVATAR_SIZE, AVATAR_SIZE), (0, 0, 0, 0))
    clipped.paste(avatar, (0, 0), mask)
    card.alpha_composite(clipped, AVATAR_POS)

    name_x, name_y = CHANNEL_NAME_POS
    name_font = font_px(FONT_BOLD, CHANNEL_NAME_FONT_SIZE)
    dc.text((name_x, name_y), channel_name, font=name_font, fill=(18, 18, 18, 255))
    name_bb = dc.textbbox((name_x, name_y), channel_name, font=name_font)
    verified = load_ui_icon("01.png", VERIFIED_ICON_SIZE)
    card.alpha_composite(
        verified,
        (int(name_bb[2] + VERIFIED_ICON_GAP), int(name_y + VERIFIED_ICON_Y_OFFSET)),
    )

    emojis = story.get("card_emojis", [])[:6]
    ix = name_x
    for emoji in emojis:
        img = render_emoji(str(emoji), EMOJI_TARGET_SIZE)
        x = int(ix + (EMOJI_CELL_SIZE - img.width) / 2)
        y = int(EMOJI_ROW_Y + (EMOJI_CELL_SIZE - img.height) / 2)
        card.alpha_composite(img, (x, y))
        ix += EMOJI_STEP_X

    hook_x, hook_y = HOOK_POS
    hook_font, hook_lines, line_height = fit_hook(
        dc, hook, HOOK_MAX_WIDTH, HOOK_MAX_HEIGHT
    )
    for i, line in enumerate(hook_lines):
        dc.text((hook_x, hook_y + i * line_height), line, font=hook_font, fill=(8, 8, 8, 255))

    light = (110, 110, 110, 255)
    meta_font = font_px(FONT_REG, FOOTER_FONT_SIZE)
    like = load_ui_icon("02.png", LIKE_ICON_SIZE)
    comment = load_ui_icon("03.png", COMMENT_ICON_SIZE)
    share = load_ui_icon("04.png", SHARE_ICON_SIZE)
    end = paste_icon_centered(card, like, LIKE_X, FOOTER_Y)
    draw_text_centered_y(dc, end + FOOTER_TEXT_GAP, FOOTER_Y, "99+", meta_font, light)
    end = paste_icon_centered(card, comment, COMMENT_X, FOOTER_Y)
    draw_text_centered_y(dc, end + FOOTER_TEXT_GAP, FOOTER_Y, "99+", meta_font, light)
    end = paste_icon_centered(card, share, SHARE_X, FOOTER_Y)
    draw_text_centered_y(dc, end + FOOTER_TEXT_GAP, FOOTER_Y, "Share", meta_font, light)

    def pill(draw, box, text, fnt, fill):
        draw.rounded_rectangle(
            box,
            radius=(box[3] - box[1]) // 2,
            fill=(0, 0, 0, 225),
            outline=(255, 255, 255, 70),
            width=PILL_OUTLINE_WIDTH,
        )
        centered_text(draw, box, text, fnt, fill)

    pill(db, HANDLE_PILL, handle, font_px(FONT_BOLD, HANDLE_FONT_SIZE), (255, 255, 255, 255))
    pill(
        db,
        SUBSCRIBE_PILL,
        "SUBSCRIBE",
        font_px(FONT_BOLD, SUBSCRIBE_FONT_SIZE),
        (255, 214, 40, 255),
    )

    card_path = OUTPUT_DIR / "story-card.png"
    brand_path = OUTPUT_DIR / "branding.png"
    contrast_path = OUTPUT_DIR / "caption-contrast.png"
    card.save(card_path)
    brand.save(brand_path)
    readability = selection.get("readability") or {}
    protection_alpha = float(readability.get("protection_alpha", 0.18))
    protection_alpha = min(0.38, max(0.18, protection_alpha))
    contrast = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    contrast_draw = ImageDraw.Draw(contrast)
    center, half = H * 0.51, H * 0.25
    for y in range(H):
        distance = abs(y - center) / half
        strength = max(0.0, 1.0 - distance * distance)
        alpha = int(round(255 * protection_alpha * strength))
        if alpha:
            contrast_draw.line((0, y, W, y), fill=(0, 0, 0, alpha))
    contrast.save(contrast_path)

    events = caption_events(narration_text, tts_segments, story_duration, start_offset=story_start)
    ass = OUTPUT_DIR / "captions.ass"
    ass.write_text(build_ass_header() + "\n".join(events) + "\n", encoding="utf-8")

    video = OUTPUT_DIR / "short.mp4"
    card_fade_start = START_LEAD_SECONDS + intro_duration
    card_fade_dur = CARD_TRANSITION_SECONDS
    filter_complex = (
        f"[0:v]fps={fps},scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},eq=brightness=-0.03:saturation=1.03[bg];"
        f"[1:v]format=rgba,fade=t=out:st={card_fade_start:.2f}:d={card_fade_dur:.2f}:alpha=1[card];"
        "[2:v]format=rgba[brand];[3:v]format=rgba[shade];"
        "[bg][shade]overlay=0:0[protected];"
        f"[protected][card]overlay=x=0:y='-{CARD_BOB_AMPLITUDE}*sin(PI*t/2)'[tmp1];"
        "[tmp1][brand]overlay=0:0[tmp2];"
        f"[tmp2]subtitles='{ass.as_posix()}',{BLACKDETECT_FILTER}[v]"
    )
    ffmpeg_command = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(background),
        "-loop", "1", "-i", str(card_path),
        "-loop", "1", "-i", str(brand_path),
        "-loop", "1", "-i", str(contrast_path),
        "-i", str(narration),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "4:a:0",
        "-t", f"{final_duration:.3f}",
        "-c:v", "libx264", "-profile:v", "high", "-preset", X264_PRESET,
        "-crf", str(X264_CRF), "-pix_fmt", "yuv420p", "-r", "30",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
    ]
    if FFMPEG_THREADS:
        ffmpeg_command.extend(["-threads", str(FFMPEG_THREADS)])
    ffmpeg_command.extend([
        "-c:a", "aac", "-ar", "48000", "-b:a", "160k",
        "-movflags", "+faststart", str(video),
    ])
    ffmpeg_duration_seconds, ffmpeg_stderr = run_capture(ffmpeg_command)
    black_durations = [
        float(value)
        for value in re.findall(r"black_duration:([0-9]+(?:\.[0-9]+)?)", ffmpeg_stderr)
    ]
    inline_blackdetect_max = max(black_durations, default=0.0)
    inline_blackdetect_passed = inline_blackdetect_max < BLACKDETECT_MAX_ALLOWED_SECONDS
    if not inline_blackdetect_passed:
        raise SystemExit(
            f"Render failed: sustained near-black section detected during encoding ({inline_blackdetect_max:.3f}s)"
        )

    resolution_started = selection.get("metrics", {}).get("resolution_started_at")
    try:
        production_elapsed = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(str(resolution_started).replace("Z", "+00:00"))
        ).total_seconds()
    except (TypeError, ValueError):
        production_elapsed = None
    speech_metrics = audio_metrics(speech_audio)
    meta = {
        "content_id": request["content_id"],
        "narration_engine": narration_cfg["engine"],
        "narration_backend_requested": narration_backend_requested,
        "narration_backend_used": narration_backend_used,
        "narration_fallback_used": narration_fallback_used,
        "narration_fallback_reason": narration_fallback_reason,
        "narration_voice": voice,
        "narration_speed": speed,
        "narration_seconds": round(speech_duration, 6),
        "narration_audio_metrics": speech_metrics,
        "card_title_text": hook,
        "start_lead_seconds": START_LEAD_SECONDS,
        "card_title_seconds": round(intro_duration, 6),
        "card_transition_seconds": CARD_TRANSITION_SECONDS,
        "story_start_seconds": round(story_start, 6),
        "story_narration_seconds": round(story_duration, 6),
        "video_seconds": round(final_duration, 6),
        "resolution": f"{W}x{H}",
        "fps": fps,
        "end_tail_seconds": END_TAIL_SECONDS,
        "production_ceiling_seconds": PRODUCTION_MAX_SECONDS,
        "test_mode": test_mode,
        "test_render_max_seconds": test_max if test_mode else None,
        "narration_excerpt": narration_text if test_mode else None,
        "ffmpeg_duration_seconds": ffmpeg_duration_seconds,
        "x264_preset": X264_PRESET,
        "x264_crf": X264_CRF,
        "h264_profile": "High",
        "pixel_format": "yuv420p",
        "color_space": "bt709",
        "audio_sample_rate": 48000,
        "background_readability_protection_applied": True,
        "background_readability_protection": "soft_caption_band",
        "background_readability_protection_alpha": protection_alpha,
        "ffmpeg_threads": FFMPEG_THREADS or "auto",
        "inline_blackdetect_passed": inline_blackdetect_passed,
        "inline_blackdetect_max_duration_seconds": round(inline_blackdetect_max, 6),
        "inline_blackdetect_filter": BLACKDETECT_FILTER,
        "inline_blackdetect_fail_threshold_seconds": BLACKDETECT_MAX_ALLOWED_SECONDS,
        "onnx_init_duration_seconds": onnx_init_duration_seconds,
        "pytorch_fallback_init_duration_seconds": pytorch_fallback_init_duration_seconds,
        "tts_generation_duration_seconds": tts_generation_duration_seconds,
        "render_process_duration_seconds": round(time.monotonic() - render_timer, 6),
        "render_started_at": render_started_at.isoformat(),
        "production_elapsed_through_render_seconds": (
            round(production_elapsed, 6) if production_elapsed is not None else None
        ),
    }
    atomic_write_json(OUTPUT_DIR / "render-metadata.json", meta)
    print(json.dumps(meta, ensure_ascii=False))
    print(video)


if __name__ == "__main__":
    main()
