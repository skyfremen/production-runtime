import json
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
            if 0.0 <= next_start - end <= 0.06:
                end = next_start
        if end <= start + 0.03:
            continue
        text = " ".join(str(item["word"]) for item in group)
        events.append(
            f"Dialogue: 0,{render.ass_time(start_offset + start)},"
            f"{render.ass_time(start_offset + end)},Main,,0,0,0,,"
            f"{render.caption_ass_text(text.upper())}"
        )
        previous_end = end
    return events


def build_caption_events(text, tts_segments, speech_duration, start_offset=0.0, narration_path=None, aligner=None):
    aligner = aligner or align_story_words
    narration_path = narration_path or (render.OUTPUT_DIR / "narration.wav")
    alignment_started = time.monotonic()
    try:
        words, align_meta = aligner(
            narration_path=narration_path,
            text=text,
            tts_segments=tts_segments,
            story_start=start_offset,
            speech_duration=speech_duration,
        )
        coverage = validate_alignment(words, text, speech_duration)
        events = aligned_caption_events(words, start_offset=start_offset)
        if not events:
            raise AlignmentError("alignment produced no caption events")
        metadata = {
            "caption_timing_mode": "word_aligned",
            "caption_alignment_backend": align_meta.get("caption_alignment_backend", ALIGNMENT_BACKEND),
            "caption_alignment_word_count": int(align_meta.get("caption_alignment_word_count", len(words))),
            "caption_alignment_coverage": round(float(coverage), 6),
            "caption_alignment_duration_seconds": round(time.monotonic() - alignment_started, 6),
            "caption_alignment_error": None,
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
        }
        return events, metadata


def caption_events_with_alignment(text, tts_segments, speech_duration, start_offset=0.0):
    global LAST_ALIGNMENT_METADATA
    events, metadata = build_caption_events(text, tts_segments, speech_duration, start_offset=start_offset)
    LAST_ALIGNMENT_METADATA = metadata
    return events


def main():
    render.caption_events = caption_events_with_alignment
    render.main()
    metadata_path = render.OUTPUT_DIR / "render-metadata.json"
    metadata = render.load_json(metadata_path)
    metadata.update(LAST_ALIGNMENT_METADATA or {
        "caption_timing_mode": "estimated_fallback",
        "caption_alignment_backend": ALIGNMENT_BACKEND,
        "caption_alignment_word_count": 0,
        "caption_alignment_coverage": 0.0,
        "caption_alignment_error": "alignment callback did not report metadata",
    })
    render.atomic_write_json(metadata_path, metadata)
    print(json.dumps({
        "caption_timing_mode": metadata["caption_timing_mode"],
        "caption_alignment_backend": metadata["caption_alignment_backend"],
        "caption_alignment_word_count": metadata["caption_alignment_word_count"],
        "caption_alignment_coverage": metadata["caption_alignment_coverage"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
