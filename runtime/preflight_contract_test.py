"""Structural regression checks for the cheap fail-before-heavy-work preflight."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print("PASS", message)


def main():
    preflight = (ROOT / "runtime/guard/preflight.py").read_text(encoding="utf-8")
    core = (ROOT / "runtime/core.py").read_text(encoding="utf-8")
    single = (ROOT / ".github/workflows/single.yml").read_text(encoding="utf-8")

    for token in (
        '"ffmpeg"',
        '"ffprobe"',
        '"espeak-ng"',
        '"onnxruntime"',
        '"kokoro_onnx"',
        '"kokoro"',
        '"torch"',
        '"torchaudio"',
        'runtime/assets/identity.png',
        'DejaVuSans-Bold.ttf',
        'NotoColorEmoji.ttf',
        'RUNTIME_RESOURCE_A',
        'RUNTIME_RESOURCE_B',
        'WAV2VEC2_ASR_BASE_960H',
        'disk_usage',
        'check_remote_access()',
    ):
        require(token in preflight, f"preflight includes {token}")

    require(
        "runtime/resources/resolve.py" not in preflight
        and "runtime/transform/process.py" not in preflight
        and "runtime/transform/verify.py" not in preflight,
        "preflight performs no heavy production stages",
    )

    preflight_pos = core.index("run_preflight()")
    pipeline_pos = core.index("ProductionPipeline(1,base_env=env).run([request])")
    require(preflight_pos < pipeline_pos, "preflight runs before ProductionPipeline heavy work")

    schema_pos = core.index("validate_request_data(data)")
    background_pos = core.index("validate_request_backgrounds")
    mapping_pos = core.index("if request not in mapping")
    require(
        schema_pos < preflight_pos and background_pos < preflight_pos and mapping_pos < preflight_pos,
        "immutable request checks run before environment preflight",
    )

    require(
        "python runtime/core.py --manifest /tmp/runtime-execution.json" in single,
        "single workflow uses the guarded core entry point",
    )
    print("PREFLIGHT_CONTRACT_TEST_PASS")


if __name__ == "__main__":
    main()
