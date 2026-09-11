import math
import os
import re
import unicodedata
from pathlib import Path

ALIGNMENT_BACKEND = "torchaudio-2.8.0/wav2vec2-base-960h-ctc"
ALIGNMENT_MIN_COVERAGE = 0.90
MODEL_SAMPLE_RATE = 16000
PAUSE_BREAK_SECONDS = 0.18

_GLUE_WORDS = {
    "A", "AN", "THE", "TO", "OF", "FOR", "WITH", "AT", "IN", "ON", "FROM",
    "AND", "BUT", "OR", "BECAUSE", "THAT", "THIS", "MY", "YOUR", "HIS",
    "HER", "THEIR", "OUR", "ITS", "AS", "BY", "IF", "WHEN", "WHILE",
}
_PHRASE_STARTERS = {
    "A", "AN", "THE", "THIS", "THAT", "THESE", "THOSE", "MY", "YOUR", "HIS",
    "HER", "THEIR", "OUR", "I", "YOU", "HE", "SHE", "WE", "THEY", "IT",
    "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE",
    "TEN",
}

_ONES = [
    "ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT",
    "NINE", "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN",
    "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN",
]
_TENS = ["", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY"]


class AlignmentError(RuntimeError):
    pass


def _integer_words(value):
    value = int(value)
    if value < 0:
        return "MINUS " + _integer_words(-value)
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens, rem = divmod(value, 10)
        return _TENS[tens] + ((" " + _ONES[rem]) if rem else "")
    if value < 1000:
        hundreds, rem = divmod(value, 100)
        return _ONES[hundreds] + " HUNDRED" + ((" " + _integer_words(rem)) if rem else "")
    if value < 1_000_000:
        thousands, rem = divmod(value, 1000)
        return _integer_words(thousands) + " THOUSAND" + ((" " + _integer_words(rem)) if rem else "")
    return " ".join(_ONES[int(ch)] for ch in str(value))


def normalize_token(token):
    text = str(token).replace("’", "'").replace("‘", "'")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.upper()
    text = text.replace("&", " AND ")
    text = re.sub(r"(?<=\d)\.(?=\d)", " POINT ", text)

    def expand_number(match):
        raw = match.group(0).replace(",", "")
        try:
            return " " + _integer_words(int(raw)) + " "
        except ValueError:
            return " ".join(_ONES[int(ch)] for ch in raw if ch.isdigit())

    text = re.sub(r"\d[\d,]*", expand_number, text)
    text = re.sub(r"[-/–—]", " ", text)
    text = re.sub(r"[^A-Z' ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("'")
    return text.replace(" ", "|")


def normalized_words(text):
    result = []
    for display in str(text).split():
        normalized = normalize_token(display)
        if normalized:
            result.append((display, normalized))
    return result


def validate_alignment(words, expected_text, speech_duration, min_coverage=ALIGNMENT_MIN_COVERAGE):
    if not isinstance(words, list) or not words:
        raise AlignmentError("alignment returned no words")
    try:
        duration = float(speech_duration)
    except (TypeError, ValueError):
        raise AlignmentError("speech duration is not numeric")
    if not math.isfinite(duration) or duration <= 0:
        raise AlignmentError("speech duration must be finite and positive")

    previous_start = -1.0
    previous_end = -1.0
    for index, item in enumerate(words):
        if not isinstance(item, dict):
            raise AlignmentError(f"word {index} is not an object")
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            raise AlignmentError(f"word {index} has invalid timestamps")
        if not math.isfinite(start) or not math.isfinite(end):
            raise AlignmentError(f"word {index} has non-finite timestamps")
        if start < -0.001 or end <= start:
            raise AlignmentError(f"word {index} has reversed or negative timestamps")
        if end > duration + 0.05:
            raise AlignmentError(f"word {index} exceeds narration duration")
        if start + 0.001 < previous_start:
            raise AlignmentError(f"word {index} starts before the previous word")
        if start + 0.05 < previous_end:
            raise AlignmentError(f"word {index} overlaps the previous word excessively")
        previous_start, previous_end = start, end

    expected = [norm for _display, norm in normalized_words(expected_text)]
    actual = [normalize_token(item.get("word", "")) for item in words]
    actual = [token for token in actual if token]
    if not expected:
        raise AlignmentError("expected narration contains no alignable words")

    cursor = 0
    matched = 0
    for token in actual:
        while cursor < len(expected) and expected[cursor] != token:
            cursor += 1
        if cursor >= len(expected):
            break
        matched += 1
        cursor += 1

    coverage = matched / len(expected)
    if coverage < float(min_coverage):
        raise AlignmentError(
            f"alignment coverage {coverage:.3f} is below minimum {float(min_coverage):.3f}"
        )
    return coverage


def _ends_with_punctuation(word, strong=False):
    raw = str(word).rstrip()
    if strong:
        return bool(re.search(r"[.!?;:]$", raw))
    return bool(re.search(r"[,!?;:.]$", raw))


def _word_key(word):
    normalized = normalize_token(word)
    return normalized.split("|", 1)[0] if normalized else ""


def _is_glue(word):
    return _word_key(word) in _GLUE_WORDS


def group_aligned_words(words, min_words=2, max_words=5):
    if not words:
        return []
    groups = []
    current = []

    for index, word in enumerate(words):
        current.append(word)
        next_word = words[index + 1] if index + 1 < len(words) else None
        count = len(current)
        should_break = False

        if next_word is None:
            should_break = True
        elif count >= min_words and _ends_with_punctuation(word.get("word", "")):
            should_break = True
        elif count >= min_words:
            pause = max(0.0, float(next_word["start"]) - float(word["end"]))
            if pause >= PAUSE_BREAK_SECONDS:
                should_break = True
            elif (
                _word_key(next_word.get("word", "")) in _PHRASE_STARTERS
                and not _is_glue(word.get("word", ""))
            ):
                should_break = True
        if not should_break and count >= 3 and not _is_glue(word.get("word", "")):
            should_break = True
        if count >= max_words:
            should_break = True

        if should_break:
            groups.append(current)
            current = []

    if current:
        groups.append(current)

    if len(groups) >= 2 and len(groups[-1]) == 1 and len(groups[-2]) < max_words:
        if not _ends_with_punctuation(groups[-2][-1].get("word", ""), strong=True):
            groups[-2].extend(groups[-1])
            groups.pop()

    return groups


def _character_targets(display_words, label_to_id):
    target_chars = []
    ranges = []
    for index, (display, normalized) in enumerate(display_words):
        if index:
            target_chars.append("|")
        start = len(target_chars)
        chars = list(normalized)
        target_chars.extend(chars)
        end = len(target_chars)
        ranges.append((display, start, end))
    if not target_chars:
        raise AlignmentError("segment contains no alignable characters")
    unsupported = sorted({char for char in target_chars if char not in label_to_id})
    if unsupported:
        raise AlignmentError(f"unsupported alignment characters: {unsupported}")
    return target_chars, ranges


def _collapse_forced_path(alignment, scores, blank_id):
    import torch

    alignment = alignment.detach().cpu()
    scores = scores.detach().cpu()
    spans = []
    current = None
    for frame, token_tensor in enumerate(alignment):
        token = int(token_tensor)
        if token == blank_id:
            if current is not None:
                spans.append(current)
                current = None
            continue
        probability = float(torch.exp(scores[frame]).item())
        if current is not None and current["token"] == token:
            current["end_frame"] = frame + 1
            current["scores"].append(probability)
        else:
            if current is not None:
                spans.append(current)
            current = {
                "token": token,
                "start_frame": frame,
                "end_frame": frame + 1,
                "scores": [probability],
            }
    if current is not None:
        spans.append(current)
    return spans


def _align_segment(model, torchaudio, segment_audio, sample_rate, display_words, labels):
    import numpy as np
    import torch

    if not len(segment_audio):
        raise AlignmentError("empty audio segment")
    label_to_id = {label: index for index, label in enumerate(labels)}
    target_chars, ranges = _character_targets(display_words, label_to_id)
    token_ids = [label_to_id[char] for char in target_chars]

    waveform = torch.as_tensor(np.asarray(segment_audio, dtype=np.float32)).reshape(1, -1)
    if int(sample_rate) != MODEL_SAMPLE_RATE:
        waveform = torchaudio.functional.resample(waveform, int(sample_rate), MODEL_SAMPLE_RATE)

    with torch.inference_mode():
        emissions, _ = model(waveform)
        log_probs = torch.log_softmax(emissions, dim=-1)
        targets = torch.tensor([token_ids], dtype=torch.int32)
        alignment, scores = torchaudio.functional.forced_align(
            log_probs, targets, blank=0
        )

    path = _collapse_forced_path(alignment[0], scores[0], blank_id=0)
    collapsed_ids = [item["token"] for item in path]
    if collapsed_ids != token_ids:
        raise AlignmentError(
            f"forced path/token mismatch: expected {len(token_ids)} chars, got {len(collapsed_ids)}"
        )

    segment_seconds = len(segment_audio) / float(sample_rate)
    frame_seconds = segment_seconds / max(1, int(alignment.shape[1]))
    words = []
    for display, start_index, end_index in ranges:
        char_spans = path[start_index:end_index]
        if not char_spans:
            raise AlignmentError(f"no character span for {display!r}")
        start = char_spans[0]["start_frame"] * frame_seconds
        end = char_spans[-1]["end_frame"] * frame_seconds
        confidence_values = [
            score for span in char_spans for score in span["scores"]
        ]
        confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values else 0.0
        )
        words.append({
            "word": display,
            "start": float(start),
            "end": float(end),
            "confidence": round(float(confidence), 6),
        })
    return words


def align_story_words(narration_path, text, tts_segments, story_start, speech_duration):
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio

    torch_threads = max(1, int(os.getenv("TORCH_NUM_THREADS", os.cpu_count() or 1)))
    torch_interop_threads = max(1, int(os.getenv("TORCH_INTEROP_THREADS", "1")))
    torch.set_num_threads(torch_threads)
    torch.set_num_interop_threads(torch_interop_threads)

    path = Path(narration_path)
    if not path.exists():
        raise AlignmentError(f"narration file does not exist: {path}")

    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if audio.ndim != 1:
        raise AlignmentError("narration audio must be mono")

    story_start_sample = int(round(float(story_start) * sample_rate))
    story_samples = int(round(float(speech_duration) * sample_rate))
    story_audio = audio[story_start_sample:story_start_sample + story_samples]
    if len(story_audio) < max(1, story_samples - 2):
        raise AlignmentError("final narration.wav is shorter than the story timing window")

    canonical = normalized_words(text)
    if not canonical:
        raise AlignmentError("story text has no alignable words")

    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    model = bundle.get_model()
    model.eval()
    labels = bundle.get_labels()

    aligned = []
    canonical_cursor = 0
    audio_cursor = 0
    for segment_index, (segment_text, segment_samples) in enumerate(tts_segments):
        segment_samples = int(segment_samples)
        if segment_samples <= 0 or not str(segment_text).strip():
            continue

        segment_pairs = normalized_words(segment_text)
        if not segment_pairs:
            audio_cursor += segment_samples
            continue

        candidate = canonical[canonical_cursor:canonical_cursor + len(segment_pairs)]
        if len(candidate) != len(segment_pairs):
            raise AlignmentError(
                f"Kokoro segment {segment_index} exceeds canonical narration word count"
            )
        if [norm for _word, norm in candidate] != [norm for _word, norm in segment_pairs]:
            raise AlignmentError(
                f"Kokoro segment {segment_index} text diverges from canonical narration"
            )

        segment_audio = story_audio[audio_cursor:audio_cursor + segment_samples]
        if len(segment_audio) < max(1, segment_samples - 2):
            raise AlignmentError(f"audio segment {segment_index} is truncated")

        segment_words = _align_segment(
            model, torchaudio, segment_audio, sample_rate, candidate, labels
        )
        segment_offset = audio_cursor / float(sample_rate)
        for item in segment_words:
            item["start"] += segment_offset
            item["end"] += segment_offset
            aligned.append(item)

        canonical_cursor += len(candidate)
        audio_cursor += segment_samples

    if canonical_cursor < len(canonical):
        raise AlignmentError(
            f"Kokoro segments covered {canonical_cursor}/{len(canonical)} canonical words"
        )

    coverage = validate_alignment(
        aligned, text, speech_duration, min_coverage=ALIGNMENT_MIN_COVERAGE
    )
    return aligned, {
        "caption_alignment_backend": ALIGNMENT_BACKEND,
        "caption_alignment_word_count": len(aligned),
        "caption_alignment_coverage": round(float(coverage), 6),
        "caption_alignment_model": "WAV2VEC2_ASR_BASE_960H",
        "caption_alignment_torch_version": str(torch.__version__),
        "caption_alignment_torchaudio_version": str(torchaudio.__version__),
        "caption_alignment_torch_threads": torch_threads,
        "caption_alignment_torch_interop_threads": torch_interop_threads,
    }

