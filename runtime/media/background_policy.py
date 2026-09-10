"""Shared production background rendition policy for Wacky Dramas.

The rendered Short is permanently 720x1280 at 30 fps. Production should therefore
prefer the cheapest provider rendition that is visually sufficient for that target,
not a 1440p/4K original. A bounded crop-fill upscale is allowed for landscape or
square clips so a normal 1080p landscape source can be used instead of forcing UHD.
Portrait clips must already cover the native 720x1280 canvas without upscaling.
"""

import math

TARGET_WIDTH = 720
TARGET_HEIGHT = 1280
TARGET_FPS = 30
SUPPORTED_TYPES = {"video/mp4"}

# 1080p-equivalent source budget. Portrait 1080x1920 and landscape 1920x1080
# are allowed; 1440p/4K sources are not production candidates.
MAX_SOURCE_PIXELS = 1920 * 1080
# 1920x1080 landscape needs ~1.185x scale to fill a 720x1280 portrait crop.
MAX_CROP_FILL_UPSCALE = 1.25


def crop_fill_geometry(width, height, target_width=TARGET_WIDTH, target_height=TARGET_HEIGHT):
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("source dimensions must be positive")
    scale = max(target_width / width, target_height / height)
    return {
        "scale_factor": scale,
        "scaled_width": math.ceil(width * scale),
        "scaled_height": math.ceil(height * scale),
        "source_crop_width": target_width / scale,
        "source_crop_height": target_height / scale,
        "upscaling_required": scale > 1.000001,
    }


def rendition_is_production_suitable(
    rendition,
    target_width=TARGET_WIDTH,
    target_height=TARGET_HEIGHT,
    max_source_pixels=MAX_SOURCE_PIXELS,
    max_upscale=MAX_CROP_FILL_UPSCALE,
):
    if rendition.get("file_type") not in SUPPORTED_TYPES:
        return False
    try:
        width, height = int(rendition["width"]), int(rendition["height"])
        if width <= 0 or height <= 0 or width * height > int(max_source_pixels):
            return False
        # A portrait source should not be upscaled when an exact/1080p portrait
        # rendition can be requested from the provider. The bounded-upscale rule
        # exists for landscape/square crop-fill, where some scaling is inherent.
        if height > width and (width < target_width or height < target_height):
            return False
        geometry = crop_fill_geometry(width, height, target_width, target_height)
    except (KeyError, TypeError, ValueError):
        return False
    return geometry["scale_factor"] <= float(max_upscale) + 1e-9


def _fps_rank(value, target_fps=TARGET_FPS):
    try:
        fps = float(value or 0)
    except (TypeError, ValueError):
        fps = 0.0
    if fps <= 0:
        return (3, 999.0)
    if abs(fps - target_fps) <= 0.05:
        return (0, 0.0)
    if 23.0 <= fps < target_fps:
        return (1, target_fps - fps)
    if fps > target_fps:
        return (2, fps - target_fps)
    return (3, target_fps - fps)


def rendition_sort_key(
    rendition,
    target_width=TARGET_WIDTH,
    target_height=TARGET_HEIGHT,
    target_fps=TARGET_FPS,
):
    """Prefer exact target, then 30fps, then the lowest decode/download cost."""
    width, height = int(rendition["width"]), int(rendition["height"])
    exact = 0 if (width, height) == (target_width, target_height) else 1
    pixel_area = width * height
    size = rendition.get("file_size_bytes")
    reliable_size = isinstance(size, int) and not isinstance(size, bool) and size > 0
    physical_size = size if reliable_size else pixel_area
    geometry = crop_fill_geometry(width, height, target_width, target_height)
    scale_distance = abs(math.log(max(geometry["scale_factor"], 1e-9)))
    return (
        exact,
        _fps_rank(rendition.get("fps"), target_fps),
        pixel_area,
        0 if reliable_size else 1,
        physical_size,
        round(scale_distance, 8),
        str(rendition.get("id", "")),
    )


def production_rendition_policy():
    return {
        "target_width": TARGET_WIDTH,
        "target_height": TARGET_HEIGHT,
        "target_fps": TARGET_FPS,
        "max_source_pixels": MAX_SOURCE_PIXELS,
        "max_crop_fill_upscale": MAX_CROP_FILL_UPSCALE,
        "portrait_upscaling_allowed": False,
        "selection": "exact_target_then_lowest_cost_1080p_bounded_landscape_crop_fill",
        "uhd_downloads_allowed": False,
    }

