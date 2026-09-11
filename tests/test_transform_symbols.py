import hashlib
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from transform.compose import render_emoji


class EmojiRenderTests(unittest.TestCase):
    # Regression: native 720p rendering must preserve the requested contextual
    # emojis, not collapse them into the old identical-dot fallback.
    def test_requested_emojis_render_as_distinct_images(self):
        icons = ["💼", "⏰", "📧", "😳", "🔥"]
        images = [render_emoji(icon, 36) for icon in icons]
        hashes = {hashlib.sha256(image.tobytes()).hexdigest() for image in images}
        self.assertEqual(len(hashes), len(icons))
        for image in images:
            self.assertIsNotNone(image.getbbox())
            self.assertGreater(image.getbbox()[2] - image.getbbox()[0], 8)


if __name__ == "__main__":
    unittest.main()
