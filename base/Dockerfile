FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HOME=/opt/huggingface \
    HF_HUB_CACHE=/opt/huggingface/hub \
    TORCH_HOME=/opt/torch-cache \
    RUNTIME_RESOURCE_HOME=/opt/runtime-resources \
    RUNTIME_RESOURCE_A=/opt/runtime-resources/a.bin \
    RUNTIME_RESOURCE_B=/opt/runtime-resources/b.bin

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       ffmpeg \
       espeak-ng \
       libsndfile1 \
       fonts-dejavu-core \
       fonts-noto-color-emoji \
       git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --index-url https://download.pytorch.org/whl/cpu \
    torch==2.8.0 torchaudio==2.8.0

COPY base/dependencies.txt /tmp/dependencies.txt
RUN pip install -r /tmp/dependencies.txt

RUN python -m spacy download en_core_web_sm

RUN mkdir -p "$RUNTIME_RESOURCE_HOME" \
    && python - <<'PY'
import os
import urllib.request
from pathlib import Path

assets = {
    Path(os.environ['RUNTIME_RESOURCE_A']): (
        'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx',
        300_000_000,
    ),
    Path(os.environ['RUNTIME_RESOURCE_B']): (
        'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin',
        20_000_000,
    ),
}
for target, (url, minimum) in assets.items():
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'runtime-builder/1.0'})
    with urllib.request.urlopen(request, timeout=240) as response, target.open('wb') as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    if target.stat().st_size < minimum:
        raise SystemExit(f'Runtime asset is suspiciously small: {target} ({target.stat().st_size} bytes)')
    print(f'Prepared {target.name}: {target.stat().st_size} bytes')
PY

RUN python - <<'PY'
import os
import numpy as np
import onnxruntime
from kokoro_onnx import Kokoro

options = onnxruntime.SessionOptions()
options.intra_op_num_threads = max(1, int(os.cpu_count() or 2))
options.inter_op_num_threads = 1
session = onnxruntime.InferenceSession(
    os.environ['RUNTIME_RESOURCE_A'],
    providers=['CPUExecutionProvider'],
    sess_options=options,
)
engine = Kokoro.from_session(session, os.environ['RUNTIME_RESOURCE_B'])
audio, sample_rate = engine.create(
    'Runtime validation.',
    voice='af_heart', speed=1.75, lang='en-us',
)
audio = np.asarray(audio, dtype=np.float32)
assert int(sample_rate) == 24000, sample_rate
assert audio.size > 0
assert np.all(np.isfinite(audio))
print(f'Runtime asset validation passed: {audio.size} samples at {sample_rate} Hz')
PY

RUN python - <<'PY'
from kokoro import KPipeline
pipeline = KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M')
for _gs, _ps, _audio in pipeline('Runtime fallback warmup.', voice='af_heart', speed=1.75):
    break
print('Fallback cache prepared')
PY

RUN python - <<'PY'
import torchaudio
bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
model = bundle.get_model()
model.eval()
print('Alignment cache prepared')
PY

ENV HF_HUB_OFFLINE=1
RUN python - <<'PY'
from huggingface_hub import is_offline_mode
from kokoro import KPipeline
assert is_offline_mode(), 'Offline mode must be active in the runtime image'
pipeline = KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M')
for _gs, _ps, _audio in pipeline('Offline validation.', voice='af_heart', speed=1.75):
    assert len(_audio) > 0
    break
else:
    raise SystemExit('Offline validation produced no audio')
print('Offline fallback validation passed')
PY

WORKDIR /workspace
