import json
import os
import subprocess
import sys
import time

from transform import compose as render
from transform.align import (
    ALIGNMENT_BACKEND,
    AlignmentError,
    align_story_words,
    group_aligned_words,
    validate_alignment,
)

_ORIGINAL_CAPTION_EVENTS = render.caption_events
LAST_ALIGNMENT_METADATA = {}

CAPTION_ACTIVE_ASS = "&H0028D6FF&"  # RGB #FFD628 in ASS BGR order.
CAPTION_BASE_ASS = "&H00FFFFFF&"
CAPTION_RAPID_WORD_SECONDS = 0.12
CAPTION_RAPID_GAP_SECONDS = 0.04
CAPTION_RAPID_MAX_WORDS = 3
CAPTION_TINY_GAP_SECONDS = 0.06


def bounded_render_capture(cmd):
    timeout = int(os.getenv("RENDER_SUBPROCESS_TIMEOUT_SECONDS", "900"))
    if not 30 <= timeout <= 3600:
        raise RuntimeError("Invalid render subprocess timeout")
    started = time.monotonic()
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Render subprocess timed out after {timeout}s"
        ) from exc
    if process.stdout:
        print(process.stdout, end="")
    if process.stderr:
        print(process.stderr, end="", file=sys.stderr)
    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            cmd,
            output=process.stdout,
            stderr=process.stderr,
        )
    return round(time.monotonic() - started, 6), process.stderr or ""


def _word_highlight_enabled():
    raw = os.getenv("CAPTION_WORD_HIGHLIGHT_ENABLED", "true").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise AlignmentError("CAPTION_WORD_HIGHLIGHT_ENABLED must be true or false")


def _dialogue(start, end, text, start_offset=0.0):
    if end <= start + 0.03:
        return None
    return (
        f"Dialogue: 0,{render.ass_time(start_offset + start)},"
        f"{render.ass_time(start_offset + end)},Main,,0,0,0,,{text}"
    )


def _caption_ass_focus_text(group, active_indices=()):
    words = [str(item["word"]).upper() for item in group]
    wrapped, event_font_size = render.caption_layout(" ".join(words))
    active = set(active_indices)
    rendered_lines = []
    cursor = 0
    for line in wrapped.splitlines():
        rendered_words = []
        for token in line.split():
            payload = render.escape_ass(token)
            if cursor in active:
                payload = (
                    f"{{\\c{CAPTION_ACTIVE_ASS}}}{payload}"
                    f"{{\\c{CAPTION_BASE_ASS}}}"
                )
            rendered_words.append(payload)
            cursor += 1
        rendered_lines.append(" ".join(rendered_words))
    if cursor != len(words):
        raise AlignmentError("caption layout changed aligned word cardinality")
    payload = r"\N".join(rendered_lines)
    if event_font_size != render.CAPTION_FONT_SIZE:
        payload = f"{{\\fs{event_font_size}}}{payload}"
    return payload


def _rapid_units(group):
    units = []
    rapid_group_count = 0
    index = 0
    while index < len(group):
        item = group[index]
        start = float(item["start"])
        end = float(item["end"])
        fast = (end - start) < CAPTION_RAPID_WORD_SECONDS
        last = index
        if fast:
            while last + 1 < len(group) and last - index + 1 < CAPTION_RAPID_MAX_WORDS:
                current = group[last]
                candidate = group[last + 1]
                candidate_start = float(candidate["start"])
                candidate_end = float(candidate["end"])
                candidate_fast = (candidate_end - candidate_start) < CAPTION_RAPID_WORD_SECONDS
                gap = max(0.0, candidate_start - float(current["end"]))
                if not candidate_fast or gap > CAPTION_RAPID_GAP_SECONDS:
                    break
                last += 1
        if last > index:
            rapid_group_count += 1
        units.append((index, last, float(group[index]["start"]), float(group[last]["end"])))
        index = last + 1
    return units, rapid_group_count


def aligned_caption_events(words, start_offset=0.0):
    groups = group_aligned_words(words)
    events = []
    previous_end = 0.0
    for index, group in enumerate(groups):
        if not group:
            continue
        start = max(previous_end, float(group[0]["start"]))
        end = float(group[-1]["end"])
        if index + 1 < len(groups):
            next_start = float(groups[index + 1][0]["start"])
            if 0.0 <= next_start - end <= CAPTION_TINY_GAP_SECONDS:
                end = next_start
        if end <= start + 0.03:
            continue
        text = " ".join(str(item["word"]) for item in group)
        event = _dialogue(
            start,
            end,
            render.caption_ass_text(text.upper()),
            start_offset=start_offset,
        )
        if event:
            events.append(event)
        previous_end = end
    return events


def highlighted_caption_events(words, start_offset=0.0):
    groups = group_aligned_words(words)
    events = []
    rapid_group_count = 0

    for group_index, group in enumerate(groups):
        if not group:
            continue
        group_start = float(group[0]["start"])
        group_end = float(group[-1]["end"])
        if group_index + 1 < len(groups):
            next_group_start = float(groups[group_index + 1][0]["start"])
            if 0.0 <= next_group_start - group_end <= CAPTION_TINY_GAP_SECONDS:
                group_end = next_group_start

        units, group_rapid_count = _rapid_units(group)
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

            event = _dialogue(
                unit_start,
                unit_end,
                _caption_ass_focus_text(group, range(first, last + 1)),
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

    return events, rapid_group_count


def build_caption_events(text, tts_segments, speech_duration, start_offset=0.0, narration_path=None, aligner=None):
    aligner = aligner or align_story_words
    narration_path = narration_path or (render.OUTPUT_DIR / "narration.wav")
    alignment_started = time.monotonic()
    highlight_enabled = True
    try:
        highlight_enabled = _word_highlight_enabled()
        words, align_meta = aligner(
            narration_path=narration_path,
            text=text,
            tts_segments=tts_segments,
            story_start=start_offset,
            speech_duration=speech_duration,
        )
        coverage = validate_alignment(words, text, speech_duration)
        if highlight_enabled:
            events, rapid_group_count = highlighted_caption_events(
                words, start_offset=start_offset
            )
        else:
            events = aligned_caption_events(words, start_offset=start_offset)
            rapid_group_count = 0
        if not events:
            raise AlignmentError("alignment produced no caption events")
        metadata = {
            "caption_timing_mode": "word_aligned",
            "caption_alignment_backend": align_meta.get("caption_alignment_backend", ALIGNMENT_BACKEND),
            "caption_alignment_word_count": int(align_meta.get("caption_alignment_word_count", len(words))),
            "caption_alignment_coverage": round(float(coverage), 6),
            "caption_alignment_duration_seconds": round(time.monotonic() - alignment_started, 6),
            "caption_alignment_error": None,
            "caption_word_highlight_enabled": bool(highlight_enabled),
            "caption_word_highlight_applied": bool(highlight_enabled),
            "caption_word_highlight_event_count": len(events) if highlight_enabled else 0,
            "caption_rapid_highlight_group_count": int(rapid_group_count),
        }
        for key, value in align_meta.items():
            if key.startswith("caption_alignment_"):
                metadata[key] = value
        return events, metadata
    except Exception as exc:
        print(f"::warning::Word-level caption alignment failed; using estimated fallback: {exc}", file=sys.stderr)
        events = _ORIGINAL_CAPTION_EVENTS(text, tts_segments, speech_duration, start_offset=start_offset)
        metadata = {
            "caption_timing_mode": "estimated_fallback",
            "caption_alignment_backend": ALIGNMENT_BACKEND,
            "caption_alignment_word_count": 0,
            "caption_alignment_coverage": 0.0,
            "caption_alignment_duration_seconds": round(time.monotonic() - alignment_started, 6),
            "caption_alignment_error": str(exc)[:500],
            "caption_word_highlight_enabled": bool(highlight_enabled),
            "caption_word_highlight_applied": False,
            "caption_word_highlight_event_count": 0,
            "caption_rapid_highlight_group_count": 0,
        }
        return events, metadata


def caption_events_with_alignment(text, tts_segments, speech_duration, start_offset=0.0):
    global LAST_ALIGNMENT_METADATA
    events, metadata = build_caption_events(text, tts_segments, speech_duration, start_offset=start_offset)
    LAST_ALIGNMENT_METADATA = metadata
    return events


def main():
    render.caption_events = caption_events_with_alignment
    render.run_capture = bounded_render_capture
    render.main()
    metadata_path = render.OUTPUT_DIR / "render-metadata.json"
    metadata = render.load_json(metadata_path)
    metadata.update(LAST_ALIGNMENT_METADATA or {
        "caption_timing_mode": "estimated_fallback",
        "caption_alignment_backend": ALIGNMENT_BACKEND,
        "caption_alignment_word_count": 0,
        "caption_alignment_coverage": 0.0,
        "caption_alignment_error": "alignment callback did not report metadata",
        "caption_word_highlight_enabled": _word_highlight_enabled(),
        "caption_word_highlight_applied": False,
        "caption_word_highlight_event_count": 0,
        "caption_rapid_highlight_group_count": 0,
    })
    render.atomic_write_json(metadata_path, metadata)
    print(json.dumps({
        "caption_timing_mode": metadata["caption_timing_mode"],
        "caption_alignment_backend": metadata["caption_alignment_backend"],
        "caption_alignment_word_count": metadata["caption_alignment_word_count"],
        "caption_alignment_coverage": metadata["caption_alignment_coverage"],
        "caption_word_highlight_applied": metadata["caption_word_highlight_applied"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
