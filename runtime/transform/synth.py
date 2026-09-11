import os
import re
import time
from pathlib import Path

import numpy as np

SAMPLE_RATE = 24000
ONNX_BACKEND = "onnx-fp32"
PYTORCH_FALLBACK_BACKEND = "pytorch-fallback"
ONNX_MODEL_PATH = Path(os.getenv("KOKORO_ONNX_MODEL", "/opt/kokoro-onnx/kokoro-v1.0.onnx"))
ONNX_VOICES_PATH = Path(os.getenv("KOKORO_ONNX_VOICES", "/opt/kokoro-onnx/voices-v1.0.bin"))
ONNX_MAX_CHUNK_WORDS = 55
MAX_CLIPPED_FRACTION = 0.001


def runtime_thread_count(name, default):
    raw = os.getenv(name, "")
    if not raw:
        return max(1, int(default))
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be a positive integer") from None
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


def sentence_chunks(text, max_words=ONNX_MAX_CHUNK_WORDS):
    """Split narration on sentence boundaries, only splitting long sentences when unavoidable."""
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", str(text).strip()) if x.strip()]
    chunks, current, count = [], [], 0
    for sentence in sentences:
        words = sentence.split()
        if len(words) > max_words:
            if current:
                chunks.append(" ".join(current))
                current, count = [], 0
            for start in range(0, len(words), max_words):
                chunks.append(" ".join(words[start:start + max_words]))
            continue
        if current and count + len(words) > max_words:
            chunks.append(" ".join(current))
            current, count = [], 0
        current.append(sentence)
        count += len(words)
    if current:
        chunks.append(" ".join(current))
    if not chunks and str(text).strip():
        chunks = [str(text).strip()]
    return chunks


def audio_metrics(audio):
    audio = np.asarray(audio, dtype=np.float32)
    if not audio.size:
        return {"peak": 0.0, "rms": 0.0, "clipped_fraction": 0.0}
    return {
        "peak": round(float(np.max(np.abs(audio))), 6),
        "rms": round(float(np.sqrt(np.mean(np.square(audio)))), 6),
        "clipped_fraction": round(float(np.mean(np.abs(audio) >= 0.999)), 8),
    }


def validate_audio(audio, sample_rate, label="Kokoro ONNX"):
    if int(sample_rate) != SAMPLE_RATE:
        raise RuntimeError(f"{label} returned unexpected sample rate {sample_rate}; expected {SAMPLE_RATE}")
    part = np.asarray(audio, dtype=np.float32)
    if not part.size:
        raise RuntimeError(f"{label} produced empty audio")
    if not np.all(np.isfinite(part)):
        raise RuntimeError(f"{label} produced non-finite audio samples")
    metrics = audio_metrics(part)
    if metrics["clipped_fraction"] > MAX_CLIPPED_FRACTION:
        raise RuntimeError(
            f"{label} produced pathological clipping fraction {metrics['clipped_fraction']:.6f}"
        )
    return part, metrics


class OnnxKokoroSynthesizer:
    backend = ONNX_BACKEND

    def __init__(self, model_path=ONNX_MODEL_PATH, voices_path=ONNX_VOICES_PATH):
        model_path = Path(model_path)
        voices_path = Path(voices_path)
        if not model_path.is_file() or model_path.stat().st_size < 100_000_000:
            raise RuntimeError(f"Kokoro ONNX FP32 model missing or invalid: {model_path}")
        if not voices_path.is_file() or voices_path.stat().st_size < 1_000_000:
            raise RuntimeError(f"Kokoro ONNX voices file missing or invalid: {voices_path}")

        import onnxruntime
        from kokoro_onnx import Kokoro

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = runtime_thread_count(
            "KOKORO_ONNX_INTRA_OP_THREADS", os.cpu_count() or 2
        )
        options.inter_op_num_threads = runtime_thread_count(
            "KOKORO_ONNX_INTER_OP_THREADS", 1
        )
        started = time.monotonic()
        session = onnxruntime.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"], sess_options=options
        )
        self.engine = Kokoro.from_session(session, str(voices_path))
        self.init_seconds = round(time.monotonic() - started, 6)
        self.model_path = str(model_path)
        self.voices_path = str(voices_path)

    def synthesize(self, text, voice, speed):
        parts, segments = [], []
        last_metrics = None
        for chunk in sentence_chunks(text):
            samples, sample_rate = self.engine.create(chunk, voice=voice, speed=speed, lang="en-us")
            part, last_metrics = validate_audio(samples, sample_rate, label="Kokoro ONNX FP32")
            parts.append(part)
            segments.append((chunk, len(part)))
        if not parts:
            raise RuntimeError("Kokoro ONNX FP32 produced no narration chunks")
        audio = np.concatenate(parts).astype(np.float32, copy=False)
        _, metrics = validate_audio(audio, SAMPLE_RATE, label="Kokoro ONNX FP32 combined audio")
        return audio, segments, metrics or last_metrics


class PytorchKokoroSynthesizer:
    backend = PYTORCH_FALLBACK_BACKEND

    def __init__(self):
        from kokoro import KPipeline

        started = time.monotonic()
        self.pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
        self.init_seconds = round(time.monotonic() - started, 6)

    def synthesize(self, text, voice, speed):
        audio_parts, segments = [], []
        for gs, _ps, segment_audio in self.pipeline(text, voice=voice, speed=speed):
            part = np.asarray(segment_audio, dtype=np.float32)
            if not part.size:
                continue
            if not np.all(np.isfinite(part)):
                raise RuntimeError("PyTorch Kokoro fallback produced non-finite audio samples")
            audio_parts.append(part)
            segments.append((str(gs).strip() if gs is not None else "", len(part)))
        if not audio_parts:
            raise RuntimeError("PyTorch Kokoro fallback produced no audio")
        audio = np.concatenate(audio_parts).astype(np.float32, copy=False)
        return audio, segments, audio_metrics(audio)

