"""Zodiac full-screen list renderer: title + every answer from frame zero.

Uses deterministic procedural night-Earth backdrop. No external downloads, network,
copyrighted imagery, YouTube credentials, Wacky code or video uploads.
"""
from __future__ import annotations
import math
import os
import random
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
try:
    from .entrypoint import HandoffRejected
except ImportError:
    from entrypoint import HandoffRejected

W, H = 1080, 1920
LEFT, RIGHT = 70, 938  # Keep right-hand Shorts controls clear.
TOP, BOTTOM = 240, 1560  # Keep bottom caption/subscribe UI clear.
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
STYLES = {"sign_results", "ranking", "grouped_elements", "trait_matches"}
SIGNS = ("aries","taurus","gemini","cancer","leo","virgo",
         "libra","scorpio","sagittarius","capricorn","aquarius","pisces")
ELEMENTS = {"FIRE": ("aries","leo","sagittarius"),
            "EARTH": ("taurus","virgo","capricorn"),
            "AIR": ("gemini","libra","aquarius"),
            "WATER": ("cancer","scorpio","pisces")}


def require(ok, msg):
    if not ok:
        raise HandoffRejected("ZODIAC_LIST_LAYOUT: " + msg)


@lru_cache(maxsize=20)
def fnt(n, bold=False):
    from pathlib import Path
    path = BOLD if bold else FONT
    require(Path(path).is_file(), "DejaVu font missing")
    return ImageFont.truetype(path, n)


def lines(draw, value, font_obj, width, cap=3):
    require(isinstance(value, str) and bool(value.strip()), "blank title/answer")
    result, current = [], ""
    for word in value.split():
        require(draw.textbbox((0,0), word, font=font_obj)[2] <= width,
                "unbreakable text outside phone-safe width")
        trial = (current + " " + word).strip()
        if draw.textbbox((0,0), trial, font=font_obj)[2] <= width:
            current = trial
        else:
            if current: result.append(current)
            current = word
    if current: result.append(current)
    require(0 < len(result) <= cap, "text requires too many lines")
    return result


def configuration(creative):
    require(creative.get("display_mode") == "full_screen_list",
            "full_screen_list requested")
    mode = creative.get("list_style", "sign_results")
    require(mode in STYLES, "unsupported list style")
    cov = creative["target_identity_and_coverage"]
    ids, mapping = cov["identities"], cov["results"]
    require(2 <= len(ids) <= 18, "list needs 2–18 rows")
    require(set(ids) == set(mapping), "identity/result coverage mismatch")
    if mode == "grouped_elements":
        require(len(ids) == 12 and set(x.lower() for x in ids) == set(SIGNS),
                "element layout must include all twelve signs")
    if mode == "ranking":
        require(len(ids) >= 5, "rankings require five or more named entries")
    return mode, ids, mapping


def layout(creative):
    mode, ids, mapping = configuration(creative)
    dummy = Image.new("RGB", (W,H))
    d = ImageDraw.Draw(dummy)
    prompt = creative["task_prompt"]
    # Strong headline; all words must be readable and present on frame zero.
    title_f = fnt(53, True)
    headline = lines(d, prompt.upper(), title_f, RIGHT-LEFT-12, 3)
    title_bottom = TOP + len(headline)*72
    require(title_bottom <= 466, "headline too large for one-screen list")
    start = max(480, title_bottom + 30)
    stop = BOTTOM
    rows = []
    if mode == "grouped_elements":
        for category, members in ELEMENTS.items():
            rows.append(("group", category, ""))
            for sign in members:
                key = next(x for x in ids if x.lower() == sign)
                rows.append(("answer", key.upper(), mapping[key]))
    else:
        for idx, identity in enumerate(ids):
            label = f"{idx+1}. {identity.upper()}" if mode == "ranking" else identity.upper()
            rows.append(("answer", label, mapping[identity]))
    row_height = (stop-start)/len(rows)
    # Avoid presenting tiny crowded results. Drop concepts that do not physically fit.
    require(row_height >= 60, "too many result rows for a phone screen")
    answer_font = 39 if row_height >= 80 else 33
    label_font = fnt(answer_font, True)
    value_font = fnt(answer_font, False)
    entries = []
    for idx,(kind,label,value) in enumerate(rows):
        y = int(start + idx*row_height + 2)
        if kind == "group":
            require(row_height >= 60, "group label too small")
            entries.append((kind,label,value,y,32))
            continue
        # Strong visual contrast: label white, result luminous mint.
        gap = 17
        label_w = d.textbbox((0,0),label,font=label_font)[2]
        value_w = d.textbbox((0,0),value,font=value_font)[2]
        if label_w + gap + value_w <= RIGHT-LEFT:
            require(y+answer_font+18 <= start+(idx+1)*row_height+4,
                    "answer row vertically clips")
            entries.append(("inline",label,value,y,answer_font))
        else:
            # A two-line answer is allowed only where the same frame remains
            # readable; no scrolling, truncation or artificially tiny font.
            require(row_height >= 106, f"answer does not fit one row: {label}")
            small = fnt(33,False)
            value_lines = lines(d,value,small,RIGHT-LEFT-16,1)
            require(d.textbbox((0,0),label,font=fnt(35,True))[2] <= RIGHT-LEFT,
                    "label out of bounds")
            entries.append(("double",label,value_lines[0],y,35))
    return {"headline":headline,"entries":entries,"mode":mode,
            "title_bottom":title_bottom,"row_height":row_height,
            "identity_count":len(ids)}


@lru_cache(maxsize=1)
def backdrop():
    """Build a dark astronomy background once; no private sources or network access."""
    nasa_path = os.environ.get("ZODIAC_NIGHT_EARTH_IMAGE", "")
    if nasa_path:
        from pathlib import Path
        require(Path(nasa_path).is_file(), "configured NASA background is missing")
        with Image.open(nasa_path) as source:
            require(source.width >= 1000 and source.height >= 500,
                    "NASA image dimensions too small")
            source = source.convert("RGB")
            # Square crop around the photographed Earth, then zoom to fill a
            # tall phone canvas while the list stays completely stationary.
            side = min(source.width, source.height)
            cx,cy=source.width//2, source.height//2
            source=source.crop((cx-side//2,cy-side//2,
                                cx+side//2,cy+side//2))
            earth=source.resize((1860,1860), Image.Resampling.LANCZOS)
            im=Image.new("RGB",(W,H),(1,4,9))
            im.paste(earth,(-390,270))
            im=ImageEnhance.Brightness(im).enhance(.68)
            night_scrim=Image.new("RGB",(W,H),(1,4,9))
            return Image.blend(im,night_scrim,.32)
    im=Image.new("RGB",(W,H),(3,7,15))
    d=ImageDraw.Draw(im)
    rng=random.Random(2077)
    for _ in range(320):
        x,y=rng.randrange(W),rng.randrange(H)
        v=rng.randint(38,160)
        d.ellipse((x,y,x+1,y+1),fill=(v//2,v,v+20 if v<230 else 240))
    # Night-Earth atmosphere and curved limb, positioned behind static list.
    px,py,pr=415,1150,830
    for width in range(30,0,-2):
        d.ellipse((px-pr-width,py-pr-width,px+pr+width,py+pr+width),
                  outline=(4,18+width,32+2*width),width=2)
    d.ellipse((px-pr,py-pr,px+pr,py+pr),fill=(5,24,36),outline=(26,123,156),width=7)
    # Faint approximate continent shapes. This is a PROCEDURAL illustration,
    # not a geographically precise NASA photograph or licensed footage.
    mask=Image.new("L",(W,H),0)
    md=ImageDraw.Draw(mask)
    continents=[
        [(160,640),(275,590),(395,690),(423,805),(365,873),(291,1001),
         (182,919),(111,778)],
        [(415,783),(552,758),(634,876),(588,994),(627,1163),
         (550,1361),(468,1250),(449,1100),(402,955)],
        [(565,602),(778,560),(941,650),(1022,775),(961,934),
         (850,962),(775,877),(655,840),(602,730)],
        [(726,1224),(817,1148),(928,1267),(950,1442),
         (833,1508),(749,1402)],
    ]
    for shape in continents:
        md.polygon(shape,fill=255)
        d.polygon(shape,fill=(11,51,54),outline=(15,63,66),width=3)
    # Blue cloud and limb details behind the text.
    for i in range(9):
        cx=460+(i%3)*195
        cy=620+(i//3)*340
        d.arc((cx-160,cy-65,cx+290,cy+100),195,330,
              fill=(14,47,63),width=2)
    # Night city lights concentrated on illustrated land, not random space.
    for _ in range(2300):
        x=rng.randrange(0,W);y=rng.randrange(500,1750)
        nx,ny=(x-px)/pr,(y-py)/pr
        if nx*nx+ny*ny>0.94 or x>930 or not mask.getpixel((x,y)):
            continue
        strength=rng.choice((49,70,90,115))
        d.ellipse((x,y,x+2,y+2),fill=(strength,min(255,strength+20),strength//3))
    # Global dark scrim: sky remains legible under every static answer.
    overlay=Image.new("RGB",(W,H),(2,5,11))
    return Image.blend(im,overlay,0.20)


def compose(creative, t, index=0):
    details = layout(creative)
    image=backdrop().copy()
    d=ImageDraw.Draw(image)
    # One subtle, seamless twinkle: no flash or distracting movement.
    period=max(1.,float(creative["duration_seconds"]))
    phase=2*math.pi*(float(t)/period)
    x,y=950,275
    glow=int(75+30*math.sin(phase))
    d.ellipse((x-3,y-3,x+3,y+3),fill=(glow,glow,190))
    y0=TOP
    for line in details["headline"]:
        d.text((LEFT,y0),line,font=fnt(53,True),fill=(255,218,67),stroke_width=2,
               stroke_fill=(0,0,0))
        y0+=72
    d.line((LEFT,details["title_bottom"]+10,RIGHT,details["title_bottom"]+10),
           fill=(133,159,180),width=2)
    for kind,label,value,y,size in details["entries"]:
        if kind=="group":
            d.text((LEFT+12,y),label+" SIGNS",font=fnt(32,True),
                   fill=(255,220,99),stroke_width=1,stroke_fill=(0,0,0))
            continue
        if kind=="double":
            d.text((LEFT+5,y),label,font=fnt(35,True),fill=(250,252,254),
                   stroke_width=2,stroke_fill=(0,0,0))
            d.text((LEFT+8,y+48),value,font=fnt(33),fill=(99,241,194),
                   stroke_width=2,stroke_fill=(0,0,0))
            continue
        f=fnt(size,True)
        d.text((LEFT+5,y),label,font=f,fill=(250,252,254),stroke_width=2,
               stroke_fill=(0,0,0))
        width=d.textbbox((0,0),label,font=f)[2]
        d.text((LEFT+5+width+17,y),value,font=fnt(size),
               fill=(102,242,191),stroke_width=2,stroke_fill=(0,0,0))
    return image
