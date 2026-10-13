"""Shared colour emoji rasterizer used by Drama and Zodiac overlays."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

EMOJI_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
]


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
