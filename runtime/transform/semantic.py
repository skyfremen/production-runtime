"""Deterministic mapping from planner semantic metadata to aligned narration words."""

from transform.align import normalized_words


def _normalized_phrase(text):
    return [normalized for _display, normalized in normalized_words(str(text or ""))]


def _matches(haystack, needle):
    if not needle or len(needle) > len(haystack):
        return []
    width = len(needle)
    return [
        index
        for index in range(len(haystack) - width + 1)
        if haystack[index:index + width] == needle
    ]


def resolve_semantic_span(words, punchline):
    """Resolve planner-provided meaning inside real aligned words without inference."""
    base = {
        "status": "missing_metadata",
        "punchline_indices": frozenset(),
        "emphasis_indices": frozenset(),
        "punchline_word_count": 0,
        "emphasis_word_count": 0,
        "punchline_start": None,
        "punchline_end": None,
        "emphasis_start": None,
        "emphasis_end": None,
    }
    if not isinstance(punchline, dict):
        return base

    aligned = [_normalized_phrase(item.get("word", "")) for item in words]
    if any(len(item) != 1 for item in aligned):
        base["status"] = "aligned_token_unsupported"
        return base
    aligned = [item[0] for item in aligned]
    punchline_words = _normalized_phrase(punchline.get("text"))
    emphasis_words = _normalized_phrase(punchline.get("emphasis_text"))
    if not punchline_words or not emphasis_words:
        return base

    punchline_locations = _matches(aligned, punchline_words)
    if not punchline_locations:
        base["status"] = "punchline_not_found"
        return base
    if len(punchline_locations) != 1:
        base["status"] = "punchline_ambiguous"
        return base

    punchline_start = punchline_locations[0]
    punchline_end = punchline_start + len(punchline_words)
    inside = aligned[punchline_start:punchline_end]
    emphasis_locations = _matches(inside, emphasis_words)
    if not emphasis_locations:
        base["status"] = "emphasis_not_found"
        return base
    if len(emphasis_locations) != 1:
        base["status"] = "emphasis_ambiguous"
        return base

    emphasis_start = punchline_start + emphasis_locations[0]
    emphasis_end = emphasis_start + len(emphasis_words)
    punchline_indices = frozenset(range(punchline_start, punchline_end))
    emphasis_indices = frozenset(range(emphasis_start, emphasis_end))
    first_punchline = words[punchline_start]
    last_punchline = words[punchline_end - 1]
    first_emphasis = words[emphasis_start]
    last_emphasis = words[emphasis_end - 1]
    return {
        "status": "matched",
        "punchline_indices": punchline_indices,
        "emphasis_indices": emphasis_indices,
        "punchline_word_count": len(punchline_indices),
        "emphasis_word_count": len(emphasis_indices),
        "punchline_start": float(first_punchline["start"]),
        "punchline_end": float(last_punchline["end"]),
        "emphasis_start": float(first_emphasis["start"]),
        "emphasis_end": float(last_emphasis["end"]),
    }
