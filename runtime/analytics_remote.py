#!/usr/bin/env python3
"""Run analytics against private state using GitHub API transport only."""
from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from analytics import run

PRIVATE_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
ALLOWED = (
    re.compile(r"^content/analytics/snapshots/analytics-[0-9TZ]+\.json$"),
    re.compile(r"^content/analytics-summary\.json$"),
    re.compile(r"^content/context\.json$"),
)


class RemoteError(RuntimeError):
    pass


class RefAdvanced(RemoteError):
    pass


def encoded(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()


class GitHubPrivateState:
    def __init__(self):
        self.repo = str(os.environ.get("PRIVATE_STATE_REPOSITORY") or "").strip()
        self.token = str(os.environ.get("PRIVATE_STATE_TOKEN") or "").strip()
        if not PRIVATE_REPO_RE.fullmatch(self.repo) or not self.token:
            raise RemoteError("Invalid private-state configuration")

    def request(self, path: str, method: str = "GET", body=None):
        req = Request(
            f"https://api.github.com/repos/{self.repo}/{path}",
            data=encoded(body) if body is not None else None,
            method=method,
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
            raise RemoteError(f"GitHub API {method} {path} failed HTTP {exc.code}") from None
        except (URLError, TimeoutError) as exc:
            raise RemoteError(f"GitHub API {method} {path} network failure: {type(exc).__name__}") from None

    def head_sha(self) -> str:
        data = self.request("git/ref/heads/main")
        sha = str(((data.get("object") or {}).get("sha")) or "")
        if not SHA40.fullmatch(sha):
            raise RemoteError("Private main ref returned invalid SHA")
        return sha

    def download_tree(self, sha: str, target: Path) -> Path:
        if not SHA40.fullmatch(sha):
            raise RemoteError("Invalid private source SHA")
        req = Request(
            f"https://api.github.com/repos/{self.repo}/tarball/{sha}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        archive = target / "state.tgz"
        try:
            with urlopen(req, timeout=120) as response, archive.open("wb") as out:
                shutil.copyfileobj(response, out)
        except HTTPError as exc:
            raise RemoteError(f"Private tarball download failed HTTP {exc.code}") from None
        except (URLError, TimeoutError) as exc:
            raise RemoteError(f"Private tarball download network failure: {type(exc).__name__}") from None

        extract = target / "extract"
        extract.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tf:
            members = tf.getmembers()
            for member in members:
                resolved = (extract / member.name).resolve()
                if extract.resolve() not in resolved.parents and resolved != extract.resolve():
                    raise RemoteError("Unsafe path in private-state tarball")
            tf.extractall(extract)
        roots = [p for p in extract.iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise RemoteError("Private-state tarball has unexpected root layout")
        return roots[0]

    def commit_files(self, base_sha: str, root: Path, paths: list[Path]) -> str:
        rels = [p.relative_to(root).as_posix() for p in paths]
        for rel in rels:
            if not any(pattern.fullmatch(rel) for pattern in ALLOWED):
                raise RemoteError(f"Analytics write scope violation: {rel}")

        current = self.head_sha()
        if current != base_sha:
            raise RefAdvanced("Private main advanced before analytics commit")

        commit = self.request(f"git/commits/{base_sha}")
        base_tree = str(((commit.get("tree") or {}).get("sha")) or "")
        if not SHA40.fullmatch(base_tree):
            raise RemoteError("Private base commit returned invalid tree SHA")

        tree_entries = []
        for path, rel in zip(paths, rels):
            content = path.read_text(encoding="utf-8")
            blob = self.request("git/blobs", "POST", {"content": content, "encoding": "utf-8"})
            blob_sha = str(blob.get("sha") or "")
            if not SHA40.fullmatch(blob_sha):
                raise RemoteError(f"Blob creation returned invalid SHA for {rel}")
            tree_entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob_sha})

        tree = self.request("git/trees", "POST", {"base_tree": base_tree, "tree": tree_entries})
        tree_sha = str(tree.get("sha") or "")
        if not SHA40.fullmatch(tree_sha):
            raise RemoteError("Tree creation returned invalid SHA")

        new_commit = self.request(
            "git/commits",
            "POST",
            {
                "message": "[analytics] refresh YouTube performance context",
                "tree": tree_sha,
                "parents": [base_sha],
            },
        )
        new_sha = str(new_commit.get("sha") or "")
        if not SHA40.fullmatch(new_sha):
            raise RemoteError("Commit creation returned invalid SHA")

        if self.head_sha() != base_sha:
            raise RefAdvanced("Private main advanced during analytics commit")
        self.request("git/refs/heads/main", "PATCH", {"sha": new_sha, "force": False})
        return new_sha


def main():
    state = GitHubPrivateState()
    last = None
    for attempt in range(1, 4):
        work = Path(tempfile.mkdtemp(prefix="wacky-analytics-"))
        try:
            base_sha = state.head_sha()
            root = state.download_tree(base_sha, work)
            paths = list(run(root))
            commit_sha = state.commit_files(base_sha, root, paths)
            print(f"Analytics remote PASS commit={commit_sha} attempt={attempt}")
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
