import re
import unicodedata

PUNCHLINE_TYPES = frozenset({
    "REVEAL",
    "REVERSAL",
    "BACKFIRE",
    "COMEBACK",
    "CONSEQUENCE",
    "CONTRADICTION",
    "ABSURDITY",
})
PUNCHLINE_REQUIRED_KEYS = frozenset({"text", "emphasis_text"})
PUNCHLINE_OPTIONAL_KEYS = frozenset({"type"})
MAX_EMPHASIS_WORDS = 5

_APOSTROPHES = str.maketrans({
    "’": "'",
    "‘": "'",
    "`": "'",
    "´": "'",
    "“": '"',
    "”": '"',
})
_TOKEN_RE = re.compile(r"[0-9A-Za-z]+(?:'[0-9A-Za-z]+)*")


def semantic_tokens(value):
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_APOSTROPHES)
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(text)]


def _matches(haystack, needle):
    if not needle or len(needle) > len(haystack):
        return []
    width = len(needle)
    return [
        index
        for index in range(len(haystack) - width + 1)
        if haystack[index:index + width] == needle
    ]


def validate_punchline(script, punchline):
    errors = []
    if not isinstance(punchline, dict):
        return ["story.punchline must be an object"]

    allowed = PUNCHLINE_REQUIRED_KEYS | PUNCHLINE_OPTIONAL_KEYS
    missing = PUNCHLINE_REQUIRED_KEYS - set(punchline)
    extra = set(punchline) - allowed
    if missing:
        errors.append("story.punchline missing fields: " + ", ".join(sorted(missing)))
    if extra:
        errors.append("story.punchline unexpected fields: " + ", ".join(sorted(extra)))

    text = str(punchline.get("text") or "").strip()
    emphasis = str(punchline.get("emphasis_text") or "").strip()
    if not text:
        errors.append("story.punchline.text must be non-empty")
    if not emphasis:
        errors.append("story.punchline.emphasis_text must be non-empty")

    kind = punchline.get("type")
    if kind is not None and kind not in PUNCHLINE_TYPES:
        errors.append("story.punchline.type is not a controlled semantic type")

    script_words = semantic_tokens(script)
    punchline_words = semantic_tokens(text)
    emphasis_words = semantic_tokens(emphasis)
    if text and not punchline_words:
        errors.append("story.punchline.text must contain semantic words")
    if emphasis and not emphasis_words:
        errors.append("story.punchline.emphasis_text must contain semantic words")
    if emphasis_words and len(emphasis_words) > MAX_EMPHASIS_WORDS:
        errors.append(
            f"story.punchline.emphasis_text must be at most {MAX_EMPHASIS_WORDS} words"
        )

    if script_words and punchline_words:
        if punchline_words == script_words:
            errors.append("story.punchline.text must not be the entire story script")
        locations = _matches(script_words, punchline_words)
        if not locations:
            errors.append("story.punchline.text must occur in story.script after normalization")
        elif len(locations) > 1:
            errors.append("story.punchline.text must identify one unambiguous span in story.script")

    if punchline_words and emphasis_words:
        locations = _matches(punchline_words, emphasis_words)
        if not locations:
            errors.append("story.punchline.emphasis_text must occur inside story.punchline.text")
        elif len(locations) > 1:
            errors.append(
                "story.punchline.emphasis_text must identify one unambiguous span inside story.punchline.text"
            )

    return errors
