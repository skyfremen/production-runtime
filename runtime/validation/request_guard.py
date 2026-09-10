import re
import subprocess


PROTECTED_PATHS = (
    "runtime/content/requests",
    "runtime/content/results",
    "runtime/content/recovery",
    "runtime/content/planning",
    "runtime/content/background-sourcing",
)


def git(args, *, check=True):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=check
    )


def commit_available(sha):
    result = git(["cat-file", "-e", f"{sha}^{{commit}}"], check=False)
    return result.returncode == 0


def ensure_commit_available(sha):
    """Make a pushed commit resolvable without weakening append-only checks."""
    if commit_available(sha):
        return
    fetch = git(
        ["fetch", "--no-tags", "--depth=1", "origin", sha],
        check=False,
    )
    if fetch.returncode != 0 or not commit_available(sha):
        detail = (fetch.stderr or fetch.stdout or "").strip()
        suffix = f" Git reported: {detail}" if detail else ""
        raise RuntimeError(
            "Cannot verify immutable production history because commit "
            f"{sha} is unavailable after checkout/fetch. This can happen after "
            "a force-push or squash-history rewrite. The guard is failing closed "
            "rather than skipping immutable-history validation."
            + suffix
        )


def check_immutable_changes(base, head):
    """Reject edits or deletions to append-only production JSON."""
    for sha in (base, head):
        if re.fullmatch(r"[0-9a-fA-F]{40}", str(sha or "")):
            ensure_commit_available(sha)
    result = git(
        [
            "diff",
            "--name-status",
            "--no-renames",
            base,
            head,
            "--",
            *PROTECTED_PATHS,
        ]
    )
    for line in result.stdout.splitlines():
        status, path = line.split("\t", 1)
        if path.endswith(".json") and status != "A":
            raise ValueError(
                "Immutable request/result/recovery/planning/background-sourcing "
                f"file changed: {path}"
            )

