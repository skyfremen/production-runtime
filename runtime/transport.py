"""Exact immutable private-state transport for Wacky Dramas V1."""
import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from base.compat import validate_contract_hash
from base.contract import CONTENT_ID_RE, EXECUTION_ID_RE

SHA40 = re.compile(r"^[0-9a-f]{40}$")
DISPATCH_ID_RE = re.compile(r"^dp-[0-9a-f]{20}$")
PRIVATE_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class TransportError(RuntimeError):
    pass


def git_blob_sha(raw):
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


class PrivateState:
    def __init__(self):
        self.repo = str(os.environ.get("PRIVATE_STATE_REPOSITORY") or "")
        self.token = str(os.environ.get("PRIVATE_STATE_TOKEN") or "")
        if not PRIVATE_REPO_RE.fullmatch(self.repo) or not self.token:
            raise TransportError("Invalid private-state configuration")

    def read(self, path, ref):
        if not SHA40.fullmatch(str(ref or "")):
            raise TransportError("Exact 40-hex private source revision is required")
        url = (
            f"https://api.github.com/repos/{self.repo}/contents/"
            f"{quote(path, safe='/')}?ref={ref}"
        )
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=45) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise TransportError(
                f"Private state missing/unreadable: {path} (HTTP {exc.code})"
            ) from None
        except (URLError, TimeoutError):
            raise TransportError(f"Private state network failure: {path}") from None
        if result.get("type") != "file" or result.get("encoding") != "base64":
            raise TransportError(f"Private state is not a file: {path}")
        try:
            raw = base64.b64decode(
                "".join(str(result.get("content") or "").split()), validate=True
            )
        except ValueError:
            raise TransportError(f"Invalid base64 private state: {path}") from None
        if result.get("sha") != git_blob_sha(raw):
            raise TransportError(f"Private state blob integrity mismatch: {path}")
        return raw


def _json(raw, label):
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TransportError(f"Invalid JSON in {label}") from None
    if not isinstance(value, dict):
        raise TransportError(f"{label} must be a JSON object")
    return value


def fetch_execution(execution_id, source_sha, contract_hash, dispatch_id, output):
    if not EXECUTION_ID_RE.fullmatch(execution_id):
        raise TransportError("Invalid execution_id")
    if not SHA40.fullmatch(source_sha):
        raise TransportError("Invalid source_sha")
    if not DISPATCH_ID_RE.fullmatch(dispatch_id):
        raise TransportError("Invalid dispatch_id")
    validate_contract_hash(contract_hash)

    state = PrivateState()
    execution_path = f"content/executions/{execution_id}.json"
    execution_raw = state.read(execution_path, source_sha)
    execution = _json(execution_raw, execution_path)

    expected = {
        "execution_version": 1,
        "execution_id": execution_id,
        "contract_hash": contract_hash,
        "dispatch_id": dispatch_id,
        "state": "prepared",
    }
    if any(execution.get(k) != v for k, v in expected.items()):
        raise TransportError("Execution fence does not match opaque dispatch inputs")

    content_id = str(execution.get("content_id") or "")
    if not CONTENT_ID_RE.fullmatch(content_id):
        raise TransportError("Execution fence has invalid content_id")
    request_path = str(execution.get("request_path") or "")
    if request_path != f"content/requests/{content_id}.json":
        raise TransportError("Execution fence has invalid request_path")
    request_source_sha = str(execution.get("request_source_sha") or "")
    request_blob_sha = str(execution.get("request_blob_sha") or "")
    if not SHA40.fullmatch(request_source_sha) or not SHA40.fullmatch(request_blob_sha):
        raise TransportError("Execution fence lacks exact immutable request identity")

    request_raw = state.read(request_path, request_source_sha)
    if git_blob_sha(request_raw) != request_blob_sha:
        raise TransportError("Immutable request blob differs from execution fence")
    request = _json(request_raw, request_path)
    if request.get("content_id") != content_id:
        raise TransportError("Immutable request content_id differs from execution fence")

    registry_path = "data/backgrounds.json"
    registry_raw = state.read(registry_path, source_sha)
    registry = _json(registry_raw, registry_path)
    if registry.get("schema_version") != 3 or not isinstance(registry.get("assets"), list):
        raise TransportError("Background registry is not canonical schema_version 3")

    local_request = Path(f"runtime/content/requests/{content_id}.json")
    local_registry = Path("runtime/data/backgrounds.json")
    local_request.parent.mkdir(parents=True, exist_ok=True)
    local_registry.parent.mkdir(parents=True, exist_ok=True)
    local_request.write_bytes(request_raw)
    local_registry.write_bytes(registry_raw)

    manifest = {
        "manifest_version": 1,
        "execution_id": execution_id,
        "content_id": content_id,
        "requests": [local_request.as_posix()],
        "registry": local_registry.as_posix(),
        "request_sources": {
            local_request.as_posix(): {
                "source_commit_sha": request_source_sha,
                "request_blob_sha": request_blob_sha,
            }
        },
        "private_execution_source_sha": source_sha,
        "contract_hash": contract_hash,
        "dispatch_id": dispatch_id,
    }
    Path(output).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Fetch PASS execution={execution_id} content={content_id}")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("fetch",))
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--contract-hash", required=True)
    parser.add_argument("--dispatch-id", required=True)
    parser.add_argument("--output", default="/tmp/runtime-execution.json")
    args = parser.parse_args()
    try:
        fetch_execution(
            args.execution_id,
            args.source_sha,
            args.contract_hash,
            args.dispatch_id,
            args.output,
        )
    except (TransportError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
