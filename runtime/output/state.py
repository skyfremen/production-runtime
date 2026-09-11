"""Append-only GitHub evidence. An upload intent is an irreversible retry fence.

Failure to observe a video is never permission to repeat videos.insert once an
intent exists. API writes use create-only Contents requests, never updates.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from base.contract import ensure_request_path_matches, validate_content_id


class RecoveryBlocked(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def blob_sha(raw):
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def encoded_json(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()


def record_path(content_id, kind):
    validate_content_id(content_id)
    if kind not in {"intent", "upload"}:
        raise ValueError("Unknown evidence kind")
    return f"youtube-shorts-bot/content/recovery/{content_id}/{kind}.json"


def receipt_path(content_id):
    validate_content_id(content_id)
    return f"youtube-shorts-bot/content/results/{content_id}.json"


@dataclass
class Stored:
    data: dict
    sha: str
    created: bool = False
    commit: str = None


class GitHubState:
    def __init__(self):
        self.repo = os.environ["PRIVATE_STATE_REPOSITORY"]
        self.token = os.environ["PRIVATE_STATE_TOKEN"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo):
            raise RecoveryBlocked("Invalid canonical state repository configuration")

    def api(self, path, method="GET", body=None):
        request = Request(
            f"https://api.github.com/repos/{self.repo}/{path}",
            data=encoded_json(body) if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    @staticmethod
    def allowed(path):
        match = re.fullmatch(
            r"youtube-shorts-bot/content/(?:recovery/([^/]+)/(?:intent|upload)|results/([^/]+))\.json",
            path,
        )
        if not match:
            raise RecoveryBlocked(
                "State operations are limited to canonical evidence and receipts"
            )
        validate_content_id(match[1] or match[2])

    def load(self, path):
        self.allowed(path)
        try:
            result = self.api(f"contents/{path}?ref=main")
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise RecoveryBlocked(
                f"Cannot read durable state (HTTP {exc.code}); upload forbidden"
            ) from None
        raw = base64.b64decode(result["content"])
        if blob_sha(raw) != result["sha"]:
            raise RecoveryBlocked("GitHub evidence blob integrity mismatch")
        return Stored(json.loads(raw), result["sha"])

    def create(self, path, data):
        self.allowed(path)
        prior = self.load(path)
        if prior:
            if prior.data != data:
                raise RecoveryBlocked(f"Immutable state already exists: {path}")
            return prior
        raw = encoded_json(data)
        try:
            result = self.api(
                f"contents/{path}",
                method="PUT",
                body={
                    "branch": "main",
                    "message": f"[skip upload] record {Path(path).stem} for {data['content_id']}",
                    "content": base64.b64encode(raw).decode(),
                },
            )
        except Exception as exc:
            raise RecoveryBlocked(
                f"Durable write not acknowledged ({type(exc).__name__}); retry recovery only"
            ) from None
        if result["content"]["sha"] != blob_sha(raw):
            raise RecoveryBlocked("Durable write returned unexpected evidence SHA")
        return Stored(
            data,
            result["content"]["sha"],
            True,
            result["commit"]["sha"],
        )


def identity_for(path, data):
    path = Path(path)
    content_id = ensure_request_path_matches(path, data)
    local_relative = f"runtime/content/requests/{content_id}.json"
    if path.resolve() != Path(local_relative).resolve():
        raise RecoveryBlocked("Publishing requires the canonical immutable request path")
    relative = f"youtube-shorts-bot/content/requests/{content_id}.json"
    source = os.environ.get("SOURCE_COMMIT_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise RecoveryBlocked("An exact immutable request source commit is required")
    raw = path.read_bytes()
    expected_blob = os.environ.get("SOURCE_REQUEST_BLOB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_blob):
        raise RecoveryBlocked("An exact immutable request blob is required")
    if blob_sha(raw) != expected_blob:
        raise RecoveryBlocked("Request differs from its immutable source commit")
    return {
        "content_id": content_id,
        "request_path": relative,
        "request_blob_sha": blob_sha(raw),
        "source_commit_sha": source,
    }


def check_identity(evidence, identity):
    for key, value in identity.items():
        if evidence.get(key) != value:
            raise RecoveryBlocked(f"Evidence {key} does not match immutable request")


def workflow_identity():
    head = os.environ.get("RUNTIME_COMMIT_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise RecoveryBlocked("An exact runtime commit is required")
    return {
        "name": os.environ.get("GITHUB_WORKFLOW", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "code_commit_sha": head,
    }
