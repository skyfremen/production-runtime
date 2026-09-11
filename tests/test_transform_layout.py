import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime"
sys.path.insert(0, str(BASE))
from transform.compose import (
    CAPTION_FONT_SIZE,
    CAPTION_MARGIN_X,
    CAPTION_MAX_WIDTH,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    FONT_BOLD,
    build_ass_header,
    caption_layout,
    fit_hook,
    wrap_caption,
)


class LayoutTests(unittest.TestCase):
    def test_acceptance_hook_is_complete_and_within_card(self):
        text = 'My boss made two hours of unpaid overtime mandatory every night, and one confirmation email ended the policy before lunch.'
        draw = ImageDraw.Draw(Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT)))
        font, lines, height = fit_hook(draw, text, 900, 221)
        self.assertEqual(' '.join(lines), text)
        self.assertLessEqual(len(lines) * height, 221)
        self.assertTrue(all(draw.textlength(line, font=font) <= 900 for line in lines))

    def test_caption_geometry_is_native_1080p_and_centered(self):
        self.assertEqual((VIDEO_WIDTH, VIDEO_HEIGHT), (1080, 1920))
        self.assertEqual(CAPTION_MARGIN_X, 128)
        self.assertEqual(CAPTION_MAX_WIDTH, 824)
        self.assertEqual(VIDEO_WIDTH - 2 * CAPTION_MARGIN_X, CAPTION_MAX_WIDTH)

        header = build_ass_header()
        self.assertIn('PlayResX: 1080', header)
        self.assertIn('PlayResY: 1920', header)
        style = next(line for line in header.splitlines() if line.startswith('Style: Main,'))
        self.assertIn(',8,2,5,128,128,0,1', style)

    def test_long_captions_are_explicitly_wrapped_inside_safe_width(self):
        font = ImageFont.truetype(FONT_BOLD, CAPTION_FONT_SIZE)
        draw = ImageDraw.Draw(Image.new('L', (1, 1)))
        for text in ('UNPAID OVERTIME MANDATORY', 'ONE CONFIRMATION EMAIL', 'EVERY NIGHT, AND'):
            wrapped = wrap_caption(text)
            self.assertEqual(' '.join(wrapped.split()), text)
            self.assertTrue(
                all(draw.textlength(line, font=font) <= CAPTION_MAX_WIDTH for line in wrapped.splitlines())
            )
        self.assertIn('\n', wrap_caption('UNPAID OVERTIME MANDATORY'))

    def test_long_single_word_scales_down_without_clipping(self):
        wrapped, size = caption_layout('MISCOMMUNICATION')
        self.assertEqual(wrapped, 'MISCOMMUNICATION')
        self.assertLess(size, CAPTION_FONT_SIZE)
        font = ImageFont.truetype(FONT_BOLD, size)
        draw = ImageDraw.Draw(Image.new('L', (1, 1)))
        self.assertLessEqual(draw.textlength(wrapped, font=font), CAPTION_MAX_WIDTH)

    def test_unrenderable_word_fails_instead_of_clipping(self):
        with self.assertRaises(ValueError):
            wrap_caption('X' * 200)
