#!/usr/bin/env python3
"""Run analytics against the planner and passive warehouse via GitHub APIs."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from analytics import now_utc, run

REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
PLANNER_PATHS = {
    "content/analytics-summary.json", "content/planner-analytics.json",
    "content/analytics-index.json", "content/context.json",
}
WAREHOUSE_PATTERNS = (
    re.compile(r"^manifest/(?:report-jobs|collection-state|schema-version)\.json$"),
    re.compile(r"^raw/[a-z0-9-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.csv$"),
    re.compile(r"^raw/[a-z0-9-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.metadata\.json$"),
    re.compile(r"^realtime/\d{4}-\d{2}-\d{2}/analytics-[0-9TZ]+\.json$"),
    re.compile(r"^realtime/legacy/analytics-[0-9TZ]+\.json$"),
    re.compile(r"^retention/[A-Za-z0-9_-]{11}/(?:72h|7d)\.json$"),
)


class RemoteError(RuntimeError):
    pass


class RefAdvanced(RemoteError):
    pass


def allowed_path(role: str, relative_path: str) -> bool:
    if relative_path.startswith(".") or "/." in relative_path:
        return False
    if role == "planner":
        return relative_path in PLANNER_PATHS
    if role == "warehouse":
        return any(pattern.fullmatch(relative_path) for pattern in WAREHOUSE_PATTERNS)
    return False


def encoded(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()


def git_blob_sha(payload: bytes) -> str:
    return hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()


class GitHubRepository:
    def __init__(self, repo: str, token: str, role: str):
        self.repo = str(repo).strip()
        self.token = str(token).strip()
        self.role = role
        if not REPOSITORY_RE.fullmatch(self.repo) or not self.token or role not in {"planner", "warehouse"}:
            raise RemoteError(f"Invalid {role} repository configuration")

    def request(self, path: str, method: str = "GET", body=None):
        req = Request(
            f"https://api.github.com/repos/{self.repo}/{path}",
            data=encoded(body) if body is not None else None, method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=60) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RemoteError(f"{self.role} GitHub API {method} {path} failed HTTP {exc.code}") from None
        except (URLError, TimeoutError) as exc:
            raise RemoteError(f"{self.role} GitHub API {method} {path} network failure: {type(exc).__name__}") from None

    def head_sha(self) -> str:
        data = self.request("git/ref/heads/main")
        sha = str(((data.get("object") or {}).get("sha")) or "")
        if not SHA40.fullmatch(sha):
            raise RemoteError(f"{self.role} main ref returned invalid SHA")
        return sha

    def download_tree(self, sha: str, target: Path) -> Path:
        if not SHA40.fullmatch(sha):
            raise RemoteError(f"Invalid {self.role} source SHA")
        req = Request(
            f"https://api.github.com/repos/{self.repo}/tarball/{sha}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        archive = target / f"{self.role}.tgz"
        try:
            with urlopen(req, timeout=120) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
        except HTTPError as exc:
            raise RemoteError(f"{self.role} tarball download failed HTTP {exc.code}") from None
        except (URLError, TimeoutError) as exc:
            raise RemoteError(f"{self.role} tarball download network failure: {type(exc).__name__}") from None

        extract = target / f"{self.role}-extract"
        extract.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                resolved = (extract / member.name).resolve()
                if extract.resolve() not in resolved.parents and resolved != extract.resolve():
                    raise RemoteError(f"Unsafe path in {self.role} tarball")
            bundle.extractall(extract)
        roots = [path for path in extract.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise RemoteError(f"{self.role} tarball has unexpected root layout")
        return roots[0]

    def commit_files(self, base_sha: str, root: Path, paths: list[Path]) -> str:
        unique_paths = sorted(set(paths))
        relative = [path.relative_to(root).as_posix() for path in unique_paths]
        for path, rel in zip(unique_paths, relative):
            if not path.is_file() or not allowed_path(self.role, rel):
                raise RemoteError(f"{self.role} analytics write scope violation: {rel}")
        if self.head_sha() != base_sha:
            raise RefAdvanced(f"{self.role} main advanced before analytics commit")

        commit = self.request(f"git/commits/{base_sha}")
        base_tree = str(((commit.get("tree") or {}).get("sha")) or "")
        if not SHA40.fullmatch(base_tree):
            raise RemoteError(f"{self.role} base commit returned invalid tree SHA")
        remote_tree = self.request(f"git/trees/{base_tree}?recursive=1")
        existing = {
            str(item.get("path")): str(item.get("sha"))
            for item in remote_tree.get("tree", []) if item.get("type") == "blob"
        }

        entries = []
        for path, rel in zip(unique_paths, relative):
            payload = path.read_bytes()
            if existing.get(rel) == git_blob_sha(payload):
                continue
            blob = self.request("git/blobs", "POST", {
                "content": base64.b64encode(payload).decode("ascii"), "encoding": "base64",
            })
            blob_sha = str(blob.get("sha") or "")
            if not SHA40.fullmatch(blob_sha):
                raise RemoteError(f"Blob creation returned invalid SHA for {rel}")
            entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob_sha})
        if not entries:
            return base_sha

        tree = self.request("git/trees", "POST", {"base_tree": base_tree, "tree": entries})
        tree_sha = str(tree.get("sha") or "")
        if not SHA40.fullmatch(tree_sha):
            raise RemoteError(f"{self.role} tree creation returned invalid SHA")
        created = self.request("git/commits", "POST", {
            "message": f"[analytics] refresh {self.role} state",
            "tree": tree_sha, "parents": [base_sha],
        })
        new_sha = str(created.get("sha") or "")
        if not SHA40.fullmatch(new_sha):
            raise RemoteError(f"{self.role} commit creation returned invalid SHA")
        if self.head_sha() != base_sha:
            raise RefAdvanced(f"{self.role} main advanced during analytics commit")
        self.request("git/refs/heads/main", "PATCH", {"sha": new_sha, "force": False})
        return new_sha


def main():
    token = str(os.environ.get("PRIVATE_STATE_TOKEN") or "").strip()
    planner = GitHubRepository(os.environ.get("PRIVATE_STATE_REPOSITORY", ""), token, "planner")
    warehouse = GitHubRepository(os.environ.get("ANALYTICS_DATA_REPOSITORY", ""), token, "warehouse")
    collected_at = now_utc()
    last = None
    for attempt in range(1, 4):
        work = Path(tempfile.mkdtemp(prefix="wacky-analytics-"))
        try:
            planner_sha = planner.head_sha()
            warehouse_sha = warehouse.head_sha()
            planner_root = planner.download_tree(planner_sha, work)
            warehouse_root = warehouse.download_tree(warehouse_sha, work)
            outputs = run(planner_root, warehouse_root, collected_at)
            warehouse_commit = warehouse.commit_files(warehouse_sha, warehouse_root, outputs.warehouse_files)
            planner_commit = planner.commit_files(planner_sha, planner_root, outputs.planner_files)
            print(
                f"Analytics remote PASS planner_commit={planner_commit} "
                f"warehouse_commit={warehouse_commit} attempt={attempt}"
            )
            return
        except RefAdvanced as exc:
            last = exc
            print(f"::warning::{exc}; retrying from latest main ({attempt}/3)")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    raise SystemExit(f"ANALYTICS_REMOTE_RETRY_EXHAUSTED {last}")


if __name__ == "__main__":
    try:
        main()
    except RemoteError as exc:
        raise SystemExit(f"ANALYTICS_REMOTE_FAILED {exc}") from None
