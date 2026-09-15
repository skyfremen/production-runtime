"""Cheap fail-fast readiness checks before expensive single-video production."""
import importlib
import os
import shutil
import tempfile
from pathlib import Path

from output.access import run_preflight as check_remote_access

MIN_FREE_BYTES = 1024 * 1024 * 1024
MIN_ONNX_MODEL_BYTES = 100_000_000
MIN_ONNX_VOICES_BYTES = 1_000_000
MIN_ALIGNMENT_MODEL_BYTES = 50_000_000

REQUIRED_EXECUTABLES = ("ffmpeg", "ffprobe", "espeak-ng")
REQUIRED_MODULES = (
    "numpy",
    "PIL",
    "soundfile",
    "onnxruntime",
    "kokoro_onnx",
    "kokoro",
    "torch",
    "torchaudio",
)
REQUIRED_ASSETS = (
    Path("runtime/assets/identity.png"),
    Path("runtime/assets/ui/01.png"),
    Path("runtime/assets/ui/02.png"),
    Path("runtime/assets/ui/03.png"),
    Path("runtime/assets/ui/04.png"),
)
REQUIRED_FONTS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)
EMOJI_FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf"),
)


def _require_file(path, minimum_bytes=1, label="file"):
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        raise RuntimeError(f"Preflight failed: missing {label}: {path}") from None
    if not path.is_file() or size < int(minimum_bytes):
        raise RuntimeError(f"Preflight failed: invalid {label}: {path}")
    return path


def check_executables():
    missing = [name for name in REQUIRED_EXECUTABLES if not shutil.which(name)]
    if missing:
        raise RuntimeError("Preflight failed: missing executable(s): " + ", ".join(missing))


def check_python_modules():
    missing = []
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    if missing:
        raise RuntimeError("Preflight failed: unavailable Python module(s): " + ", ".join(missing))


def check_assets_and_fonts():
    for path in REQUIRED_ASSETS:
        _require_file(path, 1, "runtime asset")
    for path in REQUIRED_FONTS:
        _require_file(path, 10_000, "font")
    if not any(path.is_file() and path.stat().st_size > 10_000 for path in EMOJI_FONT_CANDIDATES):
        raise RuntimeError("Preflight failed: no usable emoji font is installed")


def check_tts_resources():
    model = Path(os.getenv("RUNTIME_RESOURCE_A", "/opt/runtime-resources/a.bin"))
    voices = Path(os.getenv("RUNTIME_RESOURCE_B", "/opt/runtime-resources/b.bin"))
    _require_file(model, MIN_ONNX_MODEL_BYTES, "Kokoro ONNX model")
    _require_file(voices, MIN_ONNX_VOICES_BYTES, "Kokoro ONNX voices")


def check_alignment_cache():
    import torch
    import torchaudio

    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    bundle_path = str(getattr(bundle, "_path", "") or "").strip()
    checkpoints = Path(torch.hub.get_dir()) / "checkpoints"
    candidates = []
    if bundle_path:
        candidates.append(checkpoints / Path(bundle_path).name)
    if checkpoints.is_dir():
        candidates.extend(sorted(checkpoints.glob("*wav2vec*")))
    if not any(path.is_file() and path.stat().st_size >= MIN_ALIGNMENT_MODEL_BYTES for path in candidates):
        raise RuntimeError("Preflight failed: cached Wav2Vec2 alignment model is unavailable")


def check_temp_space(root="/tmp", minimum_bytes=MIN_FREE_BYTES):
    root = Path(root)
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix="runtime-preflight-", delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
        free = shutil.disk_usage(root).free
    except OSError as exc:
        raise RuntimeError(f"Preflight failed: temporary storage is not writable: {exc}") from None
    if free < int(minimum_bytes):
        raise RuntimeError(
            f"Preflight failed: insufficient temporary disk space ({free} bytes free; "
            f"need at least {int(minimum_bytes)})"
        )


def run_preflight():
    """Prove cheap local and remote prerequisites before media/TTS/render work."""
    check_executables()
    check_python_modules()
    check_assets_and_fonts()
    check_tts_resources()
    check_alignment_cache()
    check_temp_space()
    check_remote_access()
    print("Production preflight PASS")


if __name__ == "__main__":
    run_preflight()
