FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

LABEL org.opencontainers.image.source="https://github.com/skyfremen/production-runtime"

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

COPY base/constraints.txt /tmp/constraints.txt
RUN pip install --constraint /tmp/constraints.txt \
    --index-url https://download.pytorch.org/whl/cpu \
    torch==2.8.0+cpu torchaudio==2.8.0+cpu

COPY base/dependencies.txt /tmp/dependencies.txt
RUN pip install --constraint /tmp/constraints.txt -r /tmp/dependencies.txt

RUN python - <<'PY'
import hashlib
import urllib.request
from pathlib import Path

url = 'https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl'
expected = '1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85'
target = Path('/tmp/en_core_web_sm-3.8.0-py3-none-any.whl')
urllib.request.urlretrieve(url, target)
actual = hashlib.sha256(target.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f'spaCy model checksum mismatch: {actual}')
PY
RUN pip install /tmp/en_core_web_sm-3.8.0-py3-none-any.whl \
    && rm /tmp/en_core_web_sm-3.8.0-py3-none-any.whl

RUN mkdir -p "$RUNTIME_RESOURCE_HOME" \
    && python - <<'PY'
import os
import hashlib
import urllib.request
from pathlib import Path

assets = {
    Path(os.environ['RUNTIME_RESOURCE_A']): (
        'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx',
        300_000_000,
        '7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5',
    ),
    Path(os.environ['RUNTIME_RESOURCE_B']): (
        'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin',
        20_000_000,
        'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d',
    ),
}
for target, (url, minimum, expected_sha256) in assets.items():
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'runtime-builder/1.0'})
    with urllib.request.urlopen(request, timeout=240) as response, target.open('wb') as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    if target.stat().st_size < minimum:
        raise SystemExit(f'Runtime asset is suspiciously small: {target} ({target.stat().st_size} bytes)')
    actual_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual_sha256 != expected_sha256:
        raise SystemExit(f'Runtime asset checksum mismatch: {target.name} ({actual_sha256})')
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
import os
from pathlib import Path
from huggingface_hub import snapshot_download

repo_id = 'hexgrad/Kokoro-82M'
revision = 'f3ff3571791e39611d31c381e3a41a3af07b4987'
snapshot_download(repo_id=repo_id, revision=revision)
ref = Path(os.environ['HF_HUB_CACHE']) / 'models--hexgrad--Kokoro-82M' / 'refs' / 'main'
ref.parent.mkdir(parents=True, exist_ok=True)
ref.write_text(revision, encoding='utf-8')
PY

ENV HF_HUB_OFFLINE=1
RUN python - <<'PY'
from huggingface_hub import is_offline_mode
from kokoro import KPipeline
assert is_offline_mode(), 'Offline mode must be active before fallback warmup'
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
