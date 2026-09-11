import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

REQUEST_PREFIX = "runtime/content/requests/"
PLANNING_PREFIX = "runtime/content/planning/"
SOURCING_PREFIX = "runtime/content/background-sourcing/"


class BatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class BatchResolution:
    requests: tuple[str, ...]
    planning: tuple[str, ...]
    sourcing: tuple[str, ...]


def _run_git(args, *, cwd="."):
    try:
        return subprocess.check_output(["git", *args], cwd=cwd, text=True)
    except subprocess.CalledProcessError as exc:
        raise BatchError(
            f"git {' '.join(args)} failed with exit code {exc.returncode}"
        ) from exc


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validate_planning_and_sourcing(requests, planning, sourcing):
    if not 1 <= len(requests) <= 24:
        raise BatchError(f"Daily planning commit must add 1-24 requests; got {len(requests)}")
    if len(planning) != 1:
        raise BatchError("Daily planning commit must add exactly one immutable planning audit artifact")
    if len(sourcing) > 1:
        raise BatchError("Daily planning commit may add at most one background sourcing manifest")

    payload = _read_json(planning[0])
    expected = set(payload.get("content_ids") or [])
    actual = {Path(path).stem for path in requests}
    if expected != actual:
        raise BatchError("Planning audit content_ids do not exactly match request files")
    if payload.get("final_selected") != len(requests):
        raise BatchError("Planning audit final_selected does not match request count")

    if sourcing:
        source_payload = _read_json(sourcing[0])
        if source_payload.get("plan_date") != payload.get("plan_date"):
            raise BatchError("Background sourcing plan_date must match planning audit")
        request_by_id = {
            Path(path).stem: _read_json(path) for path in requests
        }
        referenced = {
            value
            for data in request_by_id.values()
            for value in (data.get("visual") or {}).values()
        }
        for item in source_payload.get("candidates") or []:
            logical_id = item.get("logical_id")
            required_by = set(item.get("required_by_content_ids") or [])
            if logical_id not in referenced:
                raise BatchError(f"Unreferenced sourced background in content commit: {logical_id}")
            if not required_by or not required_by <= actual:
                raise BatchError(
                    f"Invalid required_by_content_ids for sourced background {logical_id}"
                )
            for content_id in required_by:
                visuals = set((request_by_id[content_id].get("visual") or {}).values())
                if logical_id not in visuals:
                    raise BatchError(
                        f"Sourcing manifest says {content_id} needs {logical_id}, "
                        "but request does not reference it"
                    )


def resolve_push_batch(source_sha, *, cwd="."):
    rows = _run_git(
        ["diff-tree", "--no-commit-id", "--name-status", "-r", source_sha],
        cwd=cwd,
    ).splitlines()
    changed = [(row.split("\t")[0], row.split("\t")[-1]) for row in rows if "\t" in row]
    requests = [
        path for status, path in changed
        if status == "A" and path.startswith(REQUEST_PREFIX) and path.endswith(".json")
    ]
    planning = [
        path for status, path in changed
        if status == "A" and path.startswith(PLANNING_PREFIX) and path.endswith(".json")
    ]
    sourcing = [
        path for status, path in changed
        if status == "A" and path.startswith(SOURCING_PREFIX) and path.endswith(".json")
    ]
    allowed = set(requests + planning + sourcing)
    other = [path for _status, path in changed if path not in allowed]
    if other:
        raise BatchError(
            "Daily planning content commit changes non-content files: " + ", ".join(other)
        )
    _validate_planning_and_sourcing(requests, planning, sourcing)
    return BatchResolution(tuple(requests), tuple(planning), tuple(sourcing))


def resolve_manual_batch(content_ids, *, cwd="."):
    ids = [item.strip() for item in content_ids.split(",") if item.strip()]
    if not ids or len(ids) > 24:
        raise BatchError("Manual batch requires 1-24 content IDs")

    requests = [f"{REQUEST_PREFIX}{content_id}.json" for content_id in ids]
    for path in requests:
        if not Path(path).exists():
            raise BatchError(f"Missing request: {path}")
        commits = _run_git(
            ["log", "--diff-filter=A", "--format=%H", "--", path],
            cwd=cwd,
        ).splitlines()
        if not commits:
            raise BatchError(f"Cannot determine immutable source for {path}")

    needed = set()
    for path in requests:
        try:
            data = _read_json(path)
        except Exception:
            continue
        needed.update((data.get("visual") or {}).values())

    source_dir = Path(SOURCING_PREFIX.rstrip("/"))
    sourcing = []
    if source_dir.exists():
        for candidate_path in sorted(source_dir.glob("*.json")):
            try:
                payload = _read_json(candidate_path)
            except Exception:
                continue
            ids_in_manifest = {
                str(item.get("logical_id"))
                for item in payload.get("candidates", [])
                if isinstance(item, dict)
            }
            if needed & ids_in_manifest:
                sourcing.append(candidate_path.as_posix())

    return BatchResolution(tuple(requests), tuple(), tuple(dict.fromkeys(sourcing)))


def validate_explicit_batch(requests, planning, sourcing=()):
    request_paths = tuple(str(Path(path)) for path in requests)
    planning_paths = tuple(str(Path(path)) for path in planning)
    sourcing_paths = tuple(str(Path(path)) for path in sourcing)
    _validate_planning_and_sourcing(request_paths, planning_paths, sourcing_paths)
    return BatchResolution(request_paths, planning_paths, sourcing_paths)


def order_requests(requests):
    paths = tuple(str(path) for path in requests)
    content_ids = [Path(path).stem for path in paths]
    duplicates = sorted({content_id for content_id in content_ids if content_ids.count(content_id) > 1})
    if duplicates:
        raise BatchError(
            "Duplicate content_id in daily batch: " + ", ".join(duplicates)
        )
    scheduled = []
    deferred = []
    for path in paths:
        try:
            data = _read_json(path)
            raw = str((data.get("publication") or {}).get("publish_at") or "").strip()
        except Exception:
            raw = ""
        if raw:
            scheduled.append((raw, str(path)))
        else:
            deferred.append(str(path))
    slots = [slot for slot, _path in scheduled]
    if len(slots) != len(set(slots)):
        raise BatchError("Duplicate publication slot in daily batch")
    return tuple(path for _slot, path in sorted(scheduled)) + tuple(deferred)


def write_batch_files(batch, *, request_output, sourcing_output):
    ordered = order_requests(batch.requests)
    Path(request_output).write_text(
        "\n".join(ordered) + ("\n" if ordered else ""),
        encoding="utf-8",
    )
    sourcing = tuple(dict.fromkeys(batch.sourcing))
    Path(sourcing_output).write_text(
        "\n".join(sourcing) + ("\n" if sourcing else ""),
        encoding="utf-8",
    )
    return ordered


def _write_github_outputs(batch):
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as out:
        out.write(f"request_count={len(batch.requests)}\n")
        out.write(f"sourcing_manifest_count={len(batch.sourcing)}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Resolve and validate one canonical batch."
    )
    parser.add_argument("--event-name", required=True, choices=("push", "workflow_dispatch"))
    parser.add_argument("--source-sha")
    parser.add_argument("--manual-content-ids", default="")
    parser.add_argument("--request-output", default="/tmp/batch-requests.txt")
    parser.add_argument(
        "--sourcing-output", default="/tmp/background-sourcing-manifests.txt"
    )
    args = parser.parse_args()

    try:
        if args.event_name == "workflow_dispatch":
            batch = resolve_manual_batch(args.manual_content_ids)
        else:
            if not args.source_sha:
                raise BatchError("--source-sha is required for push resolution")
            batch = resolve_push_batch(args.source_sha)
        ordered = write_batch_files(
            batch,
            request_output=args.request_output,
            sourcing_output=args.sourcing_output,
        )
    except (BatchError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from None

    _write_github_outputs(batch)
    scheduled = 0
    for path in ordered:
        try:
            raw = str((_read_json(path).get("publication") or {}).get("publish_at") or "").strip()
        except Exception:
            raw = ""
        scheduled += bool(raw)
    deferred = len(ordered) - scheduled
    print(
        f"Resolved {len(batch.requests)} immutable planning requests; "
        f"sourcing manifests={len(batch.sourcing)}"
    )
    print(
        f"Global slot check passed: {scheduled} sortable requests; "
        f"{deferred} request(s) deferred to per-video validation."
    )


if __name__ == "__main__":
    main()
