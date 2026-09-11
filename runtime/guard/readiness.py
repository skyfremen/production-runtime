"""Cheap, fail-closed readiness checks shared by all production modes."""
import argparse
import importlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from errors import E_AUTH, E_PREPARE, E_RESOURCE, E_STATE
from output.state import GitHubState, RecoveryBlocked
from output.transfer import authenticated_channel, make_client, require_index_ready
from resources.validate import load_registry

DIAGNOSTIC = Path("/tmp/runtime-diagnostic.json")
MIN_FREE_BYTES = 128 * 1024 * 1024


class ReadinessError(RuntimeError):
    def __init__(self, code, retryable, check):
        super().__init__(check)
        self.code = code
        self.retryable = bool(retryable)
        self.check = str(check)


def _fail(code, retryable, check):
    raise ReadinessError(code, retryable, check)


def load_manifest(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        _fail(E_PREPARE, False, "manifest")
    if data.get("schema_version") != 1:
        _fail(E_PREPARE, False, "manifest")
    requests = data.get("requests")
    sourcing = data.get("sourcing")
    if not isinstance(requests, list) or not requests:
        _fail(E_PREPARE, False, "manifest")
    if not isinstance(sourcing, list):
        _fail(E_PREPARE, False, "manifest")
    if not str(data.get("registry") or ""):
        _fail(E_PREPARE, False, "manifest")
    return data


def check_environment(manifest):
    required = (
        "PRIVATE_STATE_TOKEN",
        "PRIVATE_STATE_REPOSITORY",
        "RUNTIME_AUTH_A",
        "RUNTIME_AUTH_B",
        "RUNTIME_AUTH_C",
    )
    if any(not str(os.environ.get(name, "")).strip() for name in required):
        _fail(E_AUTH, False, "environment")
    if manifest.get("sourcing") and not str(os.environ.get("RUNTIME_SOURCE_KEY", "")).strip():
        _fail(E_RESOURCE, False, "source")


def check_runtime_dependencies():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        _fail(E_RESOURCE, False, "runtime")
    for module in ("numpy", "PIL", "soundfile"):
        try:
            importlib.import_module(module)
        except Exception:
            _fail(E_RESOURCE, False, "runtime")

    # Preserve the existing ONNX-primary / PyTorch-fallback contract. A missing
    # primary resource must not block a viable fallback merely because readiness
    # runs before a specific Short is rendered.
    try:
        synth = importlib.import_module("transform.synth")
        model = Path(synth.ONNX_MODEL_PATH)
        voices = Path(synth.ONNX_VOICES_PATH)
        onnx_ready = (
            model.is_file()
            and model.stat().st_size >= 100_000_000
            and voices.is_file()
            and voices.stat().st_size >= 1_000_000
        )
        if onnx_ready:
            importlib.import_module("onnxruntime")
            importlib.import_module("kokoro_onnx")
        else:
            importlib.import_module("kokoro")
    except Exception:
        _fail(E_RESOURCE, False, "runtime")

    required_files = (
        Path("runtime/assets/identity.png"),
        Path("runtime/assets/ui/01.png"),
        Path("runtime/assets/ui/02.png"),
        Path("runtime/assets/ui/03.png"),
        Path("runtime/assets/ui/04.png"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    if any(not path.is_file() for path in required_files):
        _fail(E_RESOURCE, False, "runtime")


def check_filesystem(root="/tmp", min_free_bytes=MIN_FREE_BYTES):
    root_path = Path(root)
    try:
        with tempfile.NamedTemporaryFile(dir=root_path, prefix="runtime-ready-", delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
        if shutil.disk_usage(root_path).free < min_free_bytes:
            _fail(E_RESOURCE, True, "filesystem")
    except ReadinessError:
        raise
    except Exception:
        _fail(E_RESOURCE, True, "filesystem")


def check_registry(manifest):
    try:
        load_registry(manifest["registry"])
    except Exception:
        _fail(E_RESOURCE, False, "registry")


def check_private_state():
    try:
        require_index_ready(GitHubState())
    except KeyError:
        _fail(E_STATE, False, "state")
    except RecoveryBlocked as exc:
        retryable = "Cannot read durable state" in str(exc)
        _fail(E_STATE, retryable, "state")
    except Exception:
        _fail(E_STATE, True, "state")


def check_remote_channel():
    try:
        channel = authenticated_channel(make_client())
    except KeyError:
        _fail(E_AUTH, False, "auth")
    except RecoveryBlocked:
        _fail(E_AUTH, False, "auth")
    except Exception:
        _fail(E_AUTH, True, "auth")
    if not isinstance(channel, dict) or not channel.get("id"):
        _fail(E_AUTH, False, "auth")


def run_readiness(manifest_path):
    manifest = load_manifest(manifest_path)
    check_environment(manifest)
    check_runtime_dependencies()
    check_filesystem()
    check_registry(manifest)
    check_private_state()
    check_remote_channel()
    print("Readiness PASS")


def write_diagnostic(error):
    payload = {
        "schema_version": 1,
        "stage": "prepare",
        "error_code": error.code,
        "retryable": error.retryable,
        "execution_started": False,
        "verification_completed": False,
        "detail": f"readiness:{error.check}",
    }
    DIAGNOSTIC.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="/tmp/runtime-batch.json")
    args = parser.parse_args()
    try:
        run_readiness(args.manifest)
    except ReadinessError as exc:
        write_diagnostic(exc)
        print(f"Readiness FAIL: {exc.code}")
        raise SystemExit(exc.code) from None
    except Exception:
        exc = ReadinessError(E_PREPARE, True, "unexpected")
        write_diagnostic(exc)
        print(f"Readiness FAIL: {exc.code}")
        raise SystemExit(exc.code) from None


if __name__ == "__main__":
    main()
