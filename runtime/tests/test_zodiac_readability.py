"""Real raster/encode regressions for legibility over bright moving footage."""
import tempfile
import importlib.util
import shutil
from pathlib import Path
import subprocess
import unittest
from zodiac.media import encode
from test_zodiac_flow import fixture


@unittest.skipUnless(importlib.util.find_spec('PIL') and shutil.which('ffmpeg'),
                     'optional media prerequisites missing')
class ReadabilityTests(unittest.TestCase):
    def test_answers_use_opaque_white_not_low_contrast_cyan(self):
        from PIL import Image
        from zodiac.cards import render_card
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'card.png'
            render_card(fixture()['winners'][0], path)
            with Image.open(path) as image:
                colors = image.convert('RGB').crop((86, 700, 895, 1500)).getcolors(1080*1920)
                cyan_pixels = sum(n for n, c in colors if c == (119, 236, 224))
                self.assertEqual(cyan_pixels, 0, 'cyan answers disappear into blue water')

    def test_overlay_retains_black_outline_and_darkens_panel_after_encoding(self):
        from PIL import Image
        from zodiac.cards import render_card
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            card = root / 'card.png'
            layout = render_card(fixture()['winners'][0], card, overlay=True)
            with Image.open(card) as image:
                self.assertEqual(image.mode, 'RGBA')
                self.assertEqual(image.getpixel((0, 0))[3], 0)
                pixels = [c for _, c in image.crop((86, 700, 895, 1500)).getcolors(1080*1920)]
                self.assertTrue(any(p == (0, 0, 0, 255) for p in pixels), 'opaque outline missing')
                self.assertTrue(any(p == (251, 250, 251, 255) for p in pixels))
                self.assertFalse(any(p == (119, 236, 224, 255) for p in pixels))
            bg, audio, out = root/'bg.mp4', root/'music.wav', root/'video.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=c=cyan:s=1080x1920:r=30', '-t', '6', '-c:v',
                            'libx264', '-threads', '2', '-preset', 'ultrafast', str(bg)], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'sine=frequency=220:sample_rate=48000', '-t', '6', '-ac', '2', str(audio)], check=True)
            encode(card, bg, audio, out, panel=layout['answer_panel'])
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(out),
                                            '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
            frame = Image.frombytes('RGB', (1080, 1920), raw)
            x, y, width, height = layout['answer_panel']
            # Empty panel gutter must remain dark; the outside footage stays visible.
            inside = frame.getpixel((x+5, y+height//2))
            outside = frame.getpixel((10, y+height//2))
            self.assertLess(max(inside), 120)
            self.assertGreater(max(outside), 180)
            crop = frame.crop((86, 700, 895, 1500))
            self.assertTrue(any(max(p) < 20 for _, p in crop.getcolors(1080*1920)), 'black outlines keyed away')


if __name__ == '__main__':
    unittest.main()
