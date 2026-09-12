from pathlib import Path


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_section(text, start_marker, end_marker, replacement, label):
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"{label}: missing start marker {start_marker!r}")
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"{label}: missing end marker {end_marker!r}")
    return text[:start] + replacement.rstrip() + "\n\n" + text[end + 1:]


process_path = Path("runtime/transform/process.py")
process = process_path.read_text(encoding="utf-8")
process = replace_once(
    process,
    'CAPTION_PUNCHLINE_ASS = "&H00E8F4FF&"  # RGB #FFF4E8.\nCAPTION_EMPHASIS_ASS = "&H001C9FFF&"  # RGB #FF9F1C.\n',
    'CAPTION_PUNCHLINE_ASS = "&H001C9FFF&"  # RGB #FF9F1C; active punchline only.\nCAPTION_EMPHASIS_ASS = CAPTION_PUNCHLINE_ASS  # Compatibility alias for semantic diagnostics.\nCAPTION_PUNCHLINE_ACTIVE_OUTLINE = render.CAPTION_OUTLINE + 2\n',
    "caption constants",
)

process = replace_section(
    process,
    "def _caption_ass_focus_text(",
    "\ndef _rapid_units",
    r'''def _caption_ass_focus_text(
    group,
    active_indices=(),
    punchline_indices=(),
    emphasis_indices=(),
):
    words = [str(item["word"]).upper() for item in group]
    wrapped, event_font_size = render.caption_layout(" ".join(words))
    active = set(active_indices)
    punchline = set(punchline_indices)
    rendered_lines = []
    cursor = 0
    for line in wrapped.splitlines():
        rendered_words = []
        for token in line.split():
            payload = render.escape_ass(token)
            if cursor in active:
                if cursor in punchline:
                    tags = (
                        f"\\c{CAPTION_PUNCHLINE_ASS}"
                        f"\\bord{CAPTION_PUNCHLINE_ACTIVE_OUTLINE}"
                    )
                    reset = (
                        f"\\c{CAPTION_BASE_ASS}"
                        f"\\bord{render.CAPTION_OUTLINE}"
                    )
                else:
                    tags = f"\\c{CAPTION_ACTIVE_ASS}"
                    reset = f"\\c{CAPTION_BASE_ASS}"
                payload = "{" + tags + "}" + payload + "{" + reset + "}"
            rendered_words.append(payload)
            cursor += 1
        rendered_lines.append(" ".join(rendered_words))
    if cursor != len(words):
        raise AlignmentError("caption layout changed aligned word cardinality")
    payload = r"\N".join(rendered_lines)
    if event_font_size != render.CAPTION_FONT_SIZE:
        payload = f"{{\\fs{event_font_size}}}{payload}"
    return payload''',
    "caption focus renderer",
)

process = replace_section(
    process,
    "def _semantic_local_state(",
    "\ndef highlighted_caption_events",
    r'''def _semantic_local_punchline_indices(
    resolution,
    group_global_start,
    group_length,
):
    if resolution.get("status") != "matched":
        return ()
    return tuple(
        index - group_global_start
        for index in resolution["punchline_indices"]
        if group_global_start <= index < group_global_start + group_length
    )


def _semantic_split_before(resolution, group_global_start, group_length):
    if resolution.get("status") != "matched":
        return ()
    punchline = sorted(resolution["punchline_indices"])
    if not punchline:
        return ()
    boundaries = {punchline[0], punchline[-1] + 1}
    return tuple(
        boundary - group_global_start
        for boundary in boundaries
        if group_global_start < boundary < group_global_start + group_length
    )''',
    "semantic local state",
)

process = replace_section(
    process,
    "def highlighted_caption_events(",
    "\ndef _semantic_metadata",
    r'''def highlighted_caption_events(words, start_offset=0.0, semantic_resolution=None):
    groups = group_aligned_words(words)
    events = []
    rapid_group_count = 0
    semantic_resolution = semantic_resolution or {"status": "disabled"}
    group_global_start = 0

    for group_index, group in enumerate(groups):
        if not group:
            continue
        group_start = float(group[0]["start"])
        group_end = float(group[-1]["end"])
        if group_index + 1 < len(groups):
            next_group_start = float(groups[group_index + 1][0]["start"])
            if 0.0 <= next_group_start - group_end <= CAPTION_TINY_GAP_SECONDS:
                group_end = next_group_start

        units, group_rapid_count = _rapid_units(
            group,
            split_before=_semantic_split_before(
                semantic_resolution, group_global_start, len(group)
            ),
        )
        rapid_group_count += group_rapid_count
        cursor = group_start

        for unit_index, (first, last, unit_start, unit_end) in enumerate(units):
            unit_start = max(cursor, unit_start)
            if unit_start - cursor > CAPTION_TINY_GAP_SECONDS:
                event = _dialogue(
                    cursor,
                    unit_start,
                    _caption_ass_focus_text(group),
                    start_offset=start_offset,
                )
                if event:
                    events.append(event)

            next_start = None
            if unit_index + 1 < len(units):
                next_start = units[unit_index + 1][2]
            elif group_index + 1 < len(groups):
                next_start = float(groups[group_index + 1][0]["start"])

            if next_start is not None and 0.0 <= next_start - unit_end <= CAPTION_TINY_GAP_SECONDS:
                unit_end = next_start
            elif group_end - unit_end <= CAPTION_TINY_GAP_SECONDS:
                unit_end = group_end

            punchline = _semantic_local_punchline_indices(
                semantic_resolution, group_global_start, len(group)
            )
            event = _dialogue(
                unit_start,
                unit_end,
                _caption_ass_focus_text(
                    group,
                    range(first, last + 1),
                    punchline_indices=punchline,
                ),
                start_offset=start_offset,
            )
            if event:
                events.append(event)
            cursor = max(cursor, unit_end)

        if group_end - cursor > CAPTION_TINY_GAP_SECONDS:
            event = _dialogue(
                cursor,
                group_end,
                _caption_ass_focus_text(group),
                start_offset=start_offset,
            )
            if event:
                events.append(event)
        group_global_start += len(group)

    return events, rapid_group_count''',
    "highlighted caption events",
)
process_path.write_text(process, encoding="utf-8")


tests_path = Path("tests/test_align.py")
tests = tests_path.read_text(encoding="utf-8")
tests = replace_section(
    tests,
    "    def test_semantic_styling_does_not_change_caption_text_or_wrapping(self):",
    "\n    def test_semantic_match_failure_keeps_real_alignment_and_word_highlight",
    r'''    def test_active_punchline_uses_orange_without_yellow_or_reflow(self):
        group = [
            {"word": "HE", "start": 0.0, "end": 0.1},
            {"word": "DELETED", "start": 0.1, "end": 0.3},
            {"word": "THE", "start": 0.3, "end": 0.4},
            {"word": "WRONG", "start": 0.4, "end": 0.6},
            {"word": "FOLDER.", "start": 0.6, "end": 0.8},
        ]
        ordinary = _caption_ass_focus_text(group, active_indices=(3,))
        punchline = _caption_ass_focus_text(
            group,
            active_indices=(3,),
            punchline_indices=range(5),
            emphasis_indices=(3, 4),
        )
        self.assertEqual(strip_ass_overrides(ordinary), strip_ass_overrides(punchline))
        self.assertIn(CAPTION_ACTIVE_ASS, ordinary)
        self.assertNotIn(CAPTION_PUNCHLINE_ASS, ordinary)
        self.assertIn(CAPTION_PUNCHLINE_ASS, punchline)
        self.assertNotIn(CAPTION_ACTIVE_ASS, punchline)
        self.assertEqual(punchline.count(CAPTION_PUNCHLINE_ASS), 1)
        self.assertIn(f"\\bord{8 + 2}", punchline)

    def test_punchline_event_replaces_yellow_with_orange_only_while_active(self):
        words = [
            {"word": "HE", "start": 0.00, "end": 0.20},
            {"word": "HAD", "start": 0.21, "end": 0.40},
            {"word": "DELETED", "start": 0.41, "end": 0.68},
            {"word": "THE", "start": 0.69, "end": 0.84},
            {"word": "WRONG", "start": 0.85, "end": 1.10},
            {"word": "FOLDER.", "start": 1.11, "end": 1.38},
        ]
        resolved = resolve_semantic_span(words, {
            "text": "He had deleted the wrong folder.",
            "emphasis_text": "wrong folder",
            "type": "REVERSAL",
        })
        events, _rapid = highlighted_caption_events(words, semantic_resolution=resolved)
        orange_events = [event for event in events if CAPTION_PUNCHLINE_ASS in event]
        yellow_events = [event for event in events if CAPTION_ACTIVE_ASS in event]
        self.assertTrue(orange_events)
        self.assertTrue(yellow_events)
        self.assertTrue(all(CAPTION_ACTIVE_ASS not in event for event in orange_events))
        self.assertTrue(all("\\bord10" in event for event in orange_events))''',
    "semantic styling tests",
)
tests_path.write_text(tests, encoding="utf-8")
