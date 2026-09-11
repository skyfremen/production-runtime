"""Deterministic physical-rendition policy for the fixed vertical output."""

import math

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FPS = 30
SUPPORTED_TYPES = {"video/mp4"}

# UHD is allowed only when a strong landscape crop requires it.
MAX_SOURCE_PIXELS = 3840 * 2160
MAX_CROP_FILL_UPSCALE = 1.05


def crop_fill_geometry(width, height, target_width=TARGET_WIDTH, target_height=TARGET_HEIGHT):
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("source dimensions must be positive")
    target_aspect = target_width / target_height
    source_aspect = width / height
    if source_aspect >= target_aspect:
        crop_height = float(height)
        crop_width = crop_height * target_aspect
    else:
        crop_width = float(width)
        crop_height = crop_width / target_aspect
    scale = max(target_width / crop_width, target_height / crop_height)
    return {
        "scale_factor": scale,
        "scaled_width": math.ceil(width * scale),
        "scaled_height": math.ceil(height * scale),
        "source_crop_width": crop_width,
        "source_crop_height": crop_height,
        "effective_crop_width": crop_width,
        "effective_crop_height": crop_height,
        "upscaling_required": scale > 1.000001,
        "material_upscaling_required": scale > MAX_CROP_FILL_UPSCALE + 1e-9,
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
    """Prefer native vertical, then the smallest sufficient 30 fps rendition."""
    width, height = int(rendition["width"]), int(rendition["height"])
    geometry = crop_fill_geometry(width, height, target_width, target_height)
    native_vertical = 0 if width <= height and geometry["scale_factor"] <= 1.000001 else 1
    exact = 0 if (width, height) == (target_width, target_height) else 1
    pixel_area = width * height
    size = rendition.get("file_size_bytes")
    reliable_size = isinstance(size, int) and not isinstance(size, bool) and size > 0
    physical_size = size if reliable_size else pixel_area
    return (
        native_vertical,
        exact,
        _fps_rank(rendition.get("fps"), target_fps),
        pixel_area,
        0 if reliable_size else 1,
        physical_size,
        str(rendition.get("id", "")),
    )


def production_rendition_policy():
    return {
        "target_width": TARGET_WIDTH,
        "target_height": TARGET_HEIGHT,
        "target_fps": TARGET_FPS,
        "max_source_pixels": MAX_SOURCE_PIXELS,
        "max_crop_fill_upscale": MAX_CROP_FILL_UPSCALE,
        "selection": "native_vertical_then_smallest_sufficient_post_crop_rendition",
        "uhd_downloads_allowed_when_required_after_crop": True,
    }
