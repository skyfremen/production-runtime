"""Narrow, fail-closed transport for canonical private state.

The module intentionally emits no repository names, paths containing content IDs,
payloads, URLs, or HTTP response bodies. All fetched data remains runner-local.
"""
import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
REQUEST_PREFIX = "youtube-shorts-bot/content/requests/"
PLANNING_PREFIX = "youtube-shorts-bot/content/planning/"
SOURCING_PREFIX = "youtube-shorts-bot/content/background-sourcing/"
RECOVERY_BATCH_PREFIX = "youtube-shorts-bot/content/recovery/batches/"
REGISTRY_PATH = "youtube-shorts-bot/media-library/backgrounds.json"
COMPLETION_PREFIX = "youtube-shorts-bot/content/completions/"
DIAGNOSTIC_PREFIX = "youtube-shorts-bot/content/diagnostics/"
SHA_RE = re.compile(r"[0-9a-f]{40}")
BATCH_RE = re.compile(r"[br]_[0-9a-f]{30}")


class TransportError(RuntimeError):
    pass


def failure_code(command):
    return "E_LOAD_001" if command == "fetch" else "E_FINALIZE_001"


def git_blob_sha(raw):
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


class PrivateState:
    def __init__(self):
        self.repository = os.environ["PRIVATE_STATE_REPOSITORY"]
        self.token = os.environ["PRIVATE_STATE_TOKEN"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise TransportError("Invalid private state configuration")

    def api(self, endpoint, *, method="GET", body=None):
        url = f"https://api.github.com/repos/{self.repository}/{endpoint}"
        data = None if body is None else json.dumps(body).encode()
        request = Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        })
        try:
            with urlopen(request, timeout=45) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError) as exc:
            code = getattr(exc, "code", "network")
            raise TransportError(f"Private state operation failed ({code})") from None

    def content(self, path, ref):
        if not SHA_RE.fullmatch(ref):
            raise TransportError("Invalid exact source SHA")
        encoded = quote(path, safe="/")
        result = self.api(f"contents/{encoded}?ref={ref}")
        if result.get("type") != "file" or result.get("encoding") != "base64":
            raise TransportError("Expected private state file is unavailable")
        encoded_content = "".join(str(result["content"]).split())
        raw = base64.b64decode(encoded_content, validate=True)
        if git_blob_sha(raw) != result.get("sha"):
            raise TransportError("Private state blob integrity check failed")
        return raw, result["sha"]

    def current_content(self, path):
        encoded = quote(path, safe="/")
        result = self.api(f"contents/{encoded}?ref=main")
        encoded_content = "".join(str(result["content"]).split())
        raw = base64.b64decode(encoded_content, validate=True)
        if git_blob_sha(raw) != result.get("sha"):
            raise TransportError("Current private state integrity check failed")
        return raw, result["sha"]

    def create(self, path, payload, message):
        raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        encoded = quote(path, safe="/")
        try:
            existing, _sha = self.current_content(path)
        except TransportError:
            existing = None
        if existing is not None:
            if existing != raw:
                raise TransportError("Immutable private state already differs")
            return False
        self.api(f"contents/{encoded}", method="PUT", body={
            "branch": "main",
            "message": message,
            "content": base64.b64encode(raw).decode(),
        })
        return True

    def update(self, path, raw, expected_sha, message):
        encoded = quote(path, safe="/")
        self.api(f"contents/{encoded}", method="PUT", body={
            "branch": "main",
            "message": message,
            "content": base64.b64encode(raw).decode(),
            "sha": expected_sha,
        })


def safe_private_path(path):
    value = PurePosixPath(path)
    if value.is_absolute() or ".." in value.parts:
        raise TransportError("Unsafe private path")
    text = value.as_posix()
    allowed = (
        (text.startswith(REQUEST_PREFIX) and text.endswith(".json"))
        or (text.startswith(PLANNING_PREFIX) and text.endswith(".json"))
        or (text.startswith(SOURCING_PREFIX) and text.endswith(".json"))
        or text == REGISTRY_PATH
    )
    if not allowed:
        raise TransportError("Private path is outside the runtime allowlist")
    return text


def local_path(private_path):
    private_path = safe_private_path(private_path)
    suffix = private_path.removeprefix("youtube-shorts-bot/")
    target = (ROOT / suffix).resolve()
    if ROOT.resolve() not in target.parents:
        raise TransportError("Unsafe local state path")
    return target


def store_file(state, path, source_sha):
    raw, sha = state.content(safe_private_path(path), source_sha)
    target = local_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return target.as_posix(), sha


def normal_batch(state, source_sha):
    commit = state.api(f"commits/{source_sha}")
    if commit.get("sha") != source_sha:
        raise TransportError("Exact source commit mismatch")
    files = commit.get("files")
    if not isinstance(files, list) or len(files) >= 300:
        raise TransportError("Source commit file list is unavailable or truncated")
    changed = [(item.get("status"), item.get("filename", "")) for item in files]
    requests = [p for s, p in changed if s == "added" and p.startswith(REQUEST_PREFIX) and p.endswith(".json")]
    planning = [p for s, p in changed if s == "added" and p.startswith(PLANNING_PREFIX) and p.endswith(".json")]
    sourcing = [p for s, p in changed if s == "added" and p.startswith(SOURCING_PREFIX) and p.endswith(".json")]
    if set(p for _s, p in changed) != set(requests + planning + sourcing):
        raise TransportError("Source commit contains non-production changes")
    if not 1 <= len(requests) <= 24 or len(planning) != 1 or len(sourcing) > 1:
        raise TransportError("Source commit is not one canonical production batch")
    return requests, planning, sourcing, {path: source_sha for path in requests}


def recovery_batch(state, batch_id, source_sha):
    path = f"{RECOVERY_BATCH_PREFIX}{batch_id}.json"
    raw, _sha = state.content(path, source_sha)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TransportError("Malformed private recovery manifest") from None
    if payload.get("schema_version") != 1 or payload.get("batch_id") != batch_id:
        raise TransportError("Invalid private recovery manifest")
    items = payload.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 24:
        raise TransportError("Invalid recovery item count")
    requests, sources = [], {}
    for item in items:
        content_id = str(item.get("content_id", ""))
        source = str(item.get("source_commit_sha", ""))
        if not re.fullmatch(r"wd-[a-zA-Z0-9-]+", content_id) or not SHA_RE.fullmatch(source):
            raise TransportError("Invalid recovery item identity")
        path = f"{REQUEST_PREFIX}{content_id}.json"
        requests.append(path)
        sources[path] = source
    if len(requests) != len(set(requests)):
        raise TransportError("Duplicate recovery item")
    return requests, [], [], sources


def bootstrap(batch_id, source_sha, output):
    if not BATCH_RE.fullmatch(batch_id) or not SHA_RE.fullmatch(source_sha):
        raise TransportError("Invalid opaque dispatch contract")
    state = PrivateState()
    if batch_id.startswith("b_"):
        requests, planning, sourcing, sources = normal_batch(state, source_sha)
    else:
        requests, planning, sourcing, sources = recovery_batch(state, batch_id, source_sha)
    local_requests, source_map = [], {}
    for path in requests:
        local, sha = store_file(state, path, source_sha)
        local_requests.append(local)
        source_map[local] = {"source_commit_sha": sources[path], "request_blob_sha": sha}
    local_planning = [store_file(state, path, source_sha)[0] for path in planning]
    local_sourcing = [store_file(state, path, source_sha)[0] for path in sourcing]
    registry_local, registry_sha = store_file(state, REGISTRY_PATH, source_sha)
    manifest = {
        "schema_version": 1,
        "batch_id": batch_id,
        "source_sha": source_sha,
        "requests": local_requests,
        "planning": local_planning,
        "sourcing": local_sourcing,
        "request_sources": source_map,
        "registry": registry_local,
        "registry_source_blob_sha": registry_sha,
    }
    Path(output).write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Load PASS: items={len(local_requests)}")


def persist_registry(manifest_path):
    manifest = json.loads(Path(manifest_path).read_text())
    state = PrivateState()
    raw = Path(manifest["registry"]).read_bytes()
    current, current_sha = state.current_content(REGISTRY_PATH)
    if current == raw:
        print("Finalize PASS")
        return
    if git_blob_sha(current) != manifest["registry_source_blob_sha"]:
        raise TransportError("Private registry changed concurrently")
    state.update(REGISTRY_PATH, raw, current_sha, "[skip upload] update runtime media registry")
    print("Finalize PASS")


def complete(manifest_path, summary_path):
    manifest = json.loads(Path(manifest_path).read_text())
    summary = json.loads(Path(summary_path).read_text())
    payload = {
        "schema_version": 1,
        "batch_id": manifest["batch_id"],
        "source_sha": manifest["source_sha"],
        "runtime_commit_sha": os.environ["RUNTIME_COMMIT_SHA"],
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "success": int(summary.get("success", 0)),
        "failed": int(summary.get("failed", 0)),
        "skipped": int(summary.get("skipped", 0)),
    }
    state = PrivateState()
    path = f"{COMPLETION_PREFIX}{manifest['batch_id']}.json"
    try:
        existing_raw, _existing_sha = state.current_content(path)
        existing = json.loads(existing_raw)
    except TransportError:
        existing = None
    identity_keys = ("schema_version", "batch_id", "source_sha", "success", "failed", "skipped")
    if existing is not None:
        if all(existing.get(key) == payload[key] for key in identity_keys):
            print("Finalize PASS")
            return
        raise TransportError("Immutable private completion already differs")
    state.create(
        path,
        payload,
        "[production complete] record opaque batch completion",
    )
    print("Finalize PASS")


def diagnose(manifest_path, diagnostic_path):
    manifest = json.loads(Path(manifest_path).read_text())
    diagnostic = json.loads(Path(diagnostic_path).read_text())
    batch_id = manifest["batch_id"]
    run_id = os.environ.get("GITHUB_RUN_ID", "unknown")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "0")
    payload = {
        **diagnostic,
        "batch_id": batch_id,
        "source_sha": manifest["source_sha"],
        "runtime_commit_sha": os.environ["RUNTIME_COMMIT_SHA"],
        "workflow_run_id": run_id,
        "workflow_run_attempt": attempt,
    }
    PrivateState().create(
        f"{DIAGNOSTIC_PREFIX}{batch_id}/{run_id}-{attempt}.json",
        payload,
        "record runtime diagnostic",
    )
    print("Finalize PASS")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--batch-id", required=True)
    fetch.add_argument("--source-sha", required=True)
    fetch.add_argument("--output", default="/tmp/runtime-batch.json")
    registry = sub.add_parser("persist-registry")
    registry.add_argument("--manifest", default="/tmp/runtime-batch.json")
    done = sub.add_parser("complete")
    done.add_argument("--manifest", default="/tmp/runtime-batch.json")
    done.add_argument("--summary", default="/tmp/runtime-public-summary.json")
    diagnostic = sub.add_parser("diagnose")
    diagnostic.add_argument("--manifest", default="/tmp/runtime-batch.json")
    diagnostic.add_argument("--diagnostic", default="/tmp/runtime-diagnostic.json")
    args = parser.parse_args()
    if args.command == "fetch":
        bootstrap(args.batch_id, args.source_sha, args.output)
    elif args.command == "persist-registry":
        persist_registry(args.manifest)
    elif args.command == "complete":
        complete(args.manifest, args.summary)
    else:
        diagnose(args.manifest, args.diagnostic)


if __name__ == "__main__":
    try:
        main()
    except (TransportError, KeyError, OSError, ValueError, json.JSONDecodeError):
        command = os.sys.argv[1] if len(os.sys.argv) > 1 else ""
        raise SystemExit(failure_code(command)) from None
