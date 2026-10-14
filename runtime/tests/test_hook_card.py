import ast
import time
import unittest
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[1] / "transform" / "compose.py"
HOOK = ("My sister charged each bridesmaid $600 for dresses a designer had given her free. "
        "We learned it at the reception, when the designer thanked us for modeling her gift—and my sister tried to stop her from saying anything else.")
FIRST = "My sister charged each bridesmaid $600 for dresses a designer had given her free."


def compose_functions():
    source = ast.parse(COMPOSE.read_text())
    wanted = {"split_opening_hook", "generate_narration", "font_px", "wrap_pixels", "fit_hook", "preflight_hook_card"}
    nodes = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    import re
    namespace = {"re": re, "time": time}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(COMPOSE), "exec"), namespace)
    return namespace


class HookCardTests(unittest.TestCase):
    def test_long_hook_moves_remaining_sentences_to_captioned_narration(self):
        ns = compose_functions()
        self.assertIn("split_opening_hook", ns)
        first, remainder = ns["split_opening_hook"](HOOK)
        self.assertEqual(FIRST, first)
        self.assertEqual(HOOK, first + " " + remainder)
        calls = []
        def synthesize(synth, text, voice, speed):
            calls.append(text)
            return [0] * 24000, [(text, 24000)]
        ns["synthesize"] = synthesize
        result = ns["generate_narration"](object(), HOOK, "The story continues.", "af_bella", 1.75, False, 5)
        self.assertEqual([FIRST, remainder + " The story continues."], calls)
        self.assertEqual(remainder + " The story continues.", result["narration_text"])

    def test_sentence_boundary_preserves_abbreviations_decimals_and_quotes(self):
        ns = compose_functions()
        self.assertIn("split_opening_hook", ns)
        cases = [
            ('Dr. Smith charged $3.50. Then he denied it.', 'Dr. Smith charged $3.50.', 'Then he denied it.'),
            ('He said "Pay now!" Then he blocked me.', 'He said "Pay now!"', 'Then he blocked me.'),
            ("One complete hook without punctuation", "One complete hook without punctuation", ""),
            ("You paid twice? He kept both payments.", "You paid twice?", "He kept both payments."),
        ]
        for text, first, remainder in cases:
            self.assertEqual((first, remainder), ns["split_opening_hook"](text))

    def test_actual_failed_hook_card_fits_and_oversized_first_sentence_rejects(self):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            self.skipTest("Pixel-layout check requires Pillow; production image supplies it")
        ns = compose_functions()
        self.assertIn("preflight_hook_card", ns)
        ns.update(Image=Image, ImageDraw=ImageDraw, ImageFont=ImageFont,
                  FONT_BOLD="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  HOOK_MAX_FONT_SIZE=65, HOOK_MIN_FONT_SIZE=41, HOOK_LINE_GAP=9,
                  HOOK_MAX_WIDTH=900, HOOK_MAX_HEIGHT=221)
        font, lines, height = ns["preflight_hook_card"](HOOK)
        self.assertEqual(FIRST, " ".join(lines))
        self.assertLessEqual(len(lines) * height, 221)
        with self.assertRaisesRegex(ValueError, "cannot fit"):
            ns["preflight_hook_card"]("A" * 1000 + ". More story.")

if __name__ == "__main__":
    unittest.main()
