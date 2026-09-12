"""Append-only execution progress evidence for recovery liveness."""
import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from transport import PrivateState, TransportError, START_PREFIX

BATCH_RE = re.compile(r"[br]_[0-9a-f]{30}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
DISPATCH_RE = re.compile(r"d_[0-9a-f]{24}")
ALLOWED_STAGES = {"prepared", "unit_started", "unit_produced", "unit_finished", "aggregate_started"}
PRODUCTION_WORKFLOWS = {"Run", "One"}


def iso_z():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _identity(manifest):
    batch_id = str(manifest.get("batch_id", ""))
    source_sha = str(manifest.get("source_sha", ""))
    run_id = str(os.environ.get("GITHUB_RUN_ID", ""))
    run_attempt = str(os.environ.get("GITHUB_RUN_ATTEMPT", ""))
    runtime_sha = str(os.environ.get("RUNTIME_COMMIT_SHA", ""))
    if (
        not BATCH_RE.fullmatch(batch_id)
        or not SHA_RE.fullmatch(source_sha)
        or not run_id.isdigit()
        or not run_attempt.isdigit()
        or not SHA_RE.fullmatch(runtime_sha)
    ):
        raise TransportError("Invalid runtime progress identity")
    return batch_id, source_sha, run_id, run_attempt, runtime_sha


def _matching_start(state, batch_id, source_sha, run_id, run_attempt, runtime_sha):
    root = f"{START_PREFIX}{batch_id}"
    try:
        entries = state.api(f"contents/{root}?ref=main")
    except Exception:
        raise TransportError("Runtime START evidence is unavailable") from None
    if not isinstance(entries, list):
        raise TransportError("Runtime START evidence is unavailable")
    matches = []
    for entry in entries:
        dispatch_id = str(entry.get("name", ""))
        if entry.get("type") != "dir" or not DISPATCH_RE.fullmatch(dispatch_id):
            continue
        path = f"{START_PREFIX}{batch_id}/{dispatch_id}/{run_id}-{run_attempt}.json"
        try:
            raw, _sha = state.current_content(path)
            payload = json.loads(raw)
        except (TransportError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            payload.get("schema_version") == 1
            and payload.get("state") == "started"
            and payload.get("batch_id") == batch_id
            and payload.get("dispatch_id") == dispatch_id
            and payload.get("source_sha") == source_sha
            and payload.get("runtime_commit_sha") == runtime_sha
            and str(payload.get("workflow_run_id", "")) == run_id
            and str(payload.get("workflow_run_attempt", "")) == run_attempt
        ):
            matches.append((dispatch_id, payload))
    if len(matches) != 1:
        raise TransportError("Runtime progress requires one exact START record")
    return matches[0]


def record_progress(manifest, stage, shard_index=None):
    if os.environ.get("GITHUB_WORKFLOW") not in PRODUCTION_WORKFLOWS:
        print(f"Progress SKIP: {stage}")
        return
    if stage not in ALLOWED_STAGES:
        raise TransportError("Invalid runtime progress stage")
    batch_id, source_sha, run_id, run_attempt, runtime_sha = _identity(manifest)
    if shard_index is not None:
        shard_index = int(shard_index)
        if not 0 <= shard_index < 24:
            raise TransportError("Invalid runtime progress shard")
    state = PrivateState()
    dispatch_id, start = _matching_start(
        state, batch_id, source_sha, run_id, run_attempt, runtime_sha
    )
    suffix = stage if shard_index is None else f"{stage}-{shard_index:02d}"
    timestamp = iso_z()
    payload = {
        "schema_version": 1,
        "state": "started",
        "batch_id": batch_id,
        "dispatch_id": dispatch_id,
        "source_sha": source_sha,
        "contract_hash": start["contract_hash"],
        "runtime_commit_sha": runtime_sha,
        "workflow_run_id": run_id,
        "workflow_run_attempt": run_attempt,
        "started_at": timestamp,
        "progress_stage": stage,
        "shard_index": shard_index,
    }
    state.create(
        f"{START_PREFIX}{batch_id}/{dispatch_id}/{run_id}-{run_attempt}-{suffix}.json",
        payload,
        "record opaque runtime progress",
    )
    print(f"Progress PASS: {stage}")


def record_from_manifest(manifest_path, stage, shard_index=None):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    record_progress(manifest, stage, shard_index)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="/tmp/runtime-batch.json")
    parser.add_argument("--stage", choices=sorted(ALLOWED_STAGES), required=True)
    parser.add_argument("--shard-index", type=int)
    args = parser.parse_args()
    record_from_manifest(args.manifest, args.stage, args.shard_index)


if __name__ == "__main__":
    try:
        main()
    except (TransportError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        raise SystemExit("E_STATE_001") from None
