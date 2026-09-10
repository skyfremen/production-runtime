"""Public-log-safe coordinator for one bounded production batch."""
import argparse
import contextlib
import io
import json
import os
import subprocess
from pathlib import Path

from media.validate_media_library import load_registry, validate_request_backgrounds
from production.batch import BatchResolution, order_requests, validate_explicit_batch, write_batch_files
from production.pipeline import SUMMARY_PATH as PIPELINE_SUMMARY
from production.pipeline import ProductionPipeline

PUBLIC_SUMMARY = Path("/tmp/runtime-public-summary.json")
PENDING = Path("/tmp/batch-pending-verification.txt")
INTERNAL_LOG = Path("/tmp/runtime-internal.log")


class RunnerError(RuntimeError):
    pass


def silent_call(command, env=None):
    with INTERNAL_LOG.open("a", encoding="utf-8") as log:
        result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RunnerError("Internal production command failed")


def request_env(base, source_map, request):
    env = dict(base)
    identity = source_map[str(request)]
    env["SOURCE_COMMIT_SHA"] = identity["source_commit_sha"]
    env["SOURCE_REQUEST_BLOB_SHA"] = identity["request_blob_sha"]
    env["STORY_OUTPUT_DIR"] = f"runtime/output/{Path(request).stem}"
    return env


def run(manifest_path, concurrency):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    requests = manifest["requests"]
    if manifest["planning"]:
        batch = validate_explicit_batch(requests, manifest["planning"], manifest["sourcing"])
        ordered = write_batch_files(
            batch,
            request_output="/tmp/batch-requests.txt",
            sourcing_output="/tmp/background-sourcing-manifests.txt",
        )
    else:
        ordered = order_requests(requests)
        if not 1 <= len(ordered) <= 24:
            raise RunnerError("Recovery batch size is outside the allowed range")
        Path("/tmp/batch-requests.txt").write_text("\n".join(ordered) + "\n")
        Path("/tmp/background-sourcing-manifests.txt").write_text("")

    registry = load_registry(manifest["registry"])
    for request in ordered:
        payload = json.loads(Path(request).read_text(encoding="utf-8"))
        validate_request_backgrounds(payload, registry)
    print(f"Batch integrity PASS: items={len(ordered)}")

    silent_call(["python", "runtime/publishing/auth_preflight.py"])
    print("Publication readiness PASS")

    for sourcing in manifest["sourcing"]:
        silent_call([
            "python", "runtime/media/pexels_registry.py", "ingest-manifest", "--manifest", sourcing,
        ])
    if manifest["sourcing"]:
        silent_call(["python", "runtime/media/validate_media_library.py"])
        silent_call([
            "python", "runtime/state_transport.py", "persist-registry", "--manifest", manifest_path,
        ])

    base_env = dict(os.environ)
    base_env["REQUEST_SOURCE_MAP_JSON"] = json.dumps(manifest["request_sources"], separators=(",", ":"))
    pipeline = ProductionPipeline(concurrency, base_env=base_env)
    with INTERNAL_LOG.open("a", encoding="utf-8") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        production = pipeline.run(ordered)

    failed_stages = {}
    failures = Path("/tmp/batch-failures.txt")
    if failures.exists():
        for line in failures.read_text(encoding="utf-8").splitlines():
            parts = line.split(" | ", 2)
            if len(parts) >= 2:
                failed_stages[parts[0]] = parts[1].split(" ", 1)[0]

    success = 0
    failed = int(production["failed"])
    pending = []
    if PENDING.exists():
        pending = [line.split("\t", 1)[0] for line in PENDING.read_text().splitlines() if line]
    for ordinal, request in enumerate(pending, 1):
        env = request_env(base_env, manifest["request_sources"], request)
        try:
            silent_call(["python", "runtime/publishing/verify_publication.py", "--request", request], env)
            silent_call(["python", "runtime/publishing/finalize_receipt.py", "--request", request], env)
            success += 1
            print(f"item {ordinal:02d} verification PASS")
        except RunnerError:
            failed += 1
            print(f"item {ordinal:02d} verification FAIL")

    summary = {
        "request_count": len(ordered),
        "success": success,
        "failed": failed,
        "skipped": int(production["skipped"]),
        "concurrency": int(production["concurrency"]),
        "production_phase_seconds": float(production["production_phase_seconds"]),
        "peak_cpu_percent": float(production["peak_cpu_percent"]),
        "peak_memory_percent": float(production["peak_memory_percent"]),
    }
    PUBLIC_SUMMARY.write_text(json.dumps(summary, sort_keys=True) + "\n")
    for ordinal, request in enumerate(ordered, 1):
        if request in failed_stages:
            print(f"item {ordinal:02d} {failed_stages[request]} FAIL")
    print(
        "batch success={success} failed={failed} skipped={skipped}".format(**summary)
    )
    if failed:
        raise RunnerError("One or more items failed; recovery remains authoritative")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="/tmp/runtime-batch.json")
    parser.add_argument("--concurrency", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    run(args.manifest, args.concurrency)


if __name__ == "__main__":
    try:
        main()
    except (RunnerError, KeyError, OSError, ValueError, json.JSONDecodeError):
        raise SystemExit("Production execution failed; inspect private recovery state") from None
