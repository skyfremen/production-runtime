"""Combine low-information unit summaries into one authoritative batch outcome."""
import argparse
import json
from pathlib import Path

from engine.batch import ordered_manifest_requests
from engine.shard import select_shard, shard_plan
from errors import E_EXEC

PUBLIC_SUMMARY = Path("/tmp/runtime-public-summary.json")
DIAGNOSTIC = Path("/tmp/runtime-diagnostic.json")
SUMMARY_FIELDS = {
    "request_count", "success", "failed", "skipped", "concurrency",
    "production_phase_seconds", "peak_cpu_percent", "peak_memory_percent",
    "shard_index",
}


class AggregateError(RuntimeError):
    pass


def _read_unit(root, index):
    matches = list(Path(root).glob(f"unit-{index}/runtime-public-summary.json"))
    if len(matches) != 1:
        return None
    try:
        value = json.loads(matches[0].read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or set(value) != SUMMARY_FIELDS:
        return None
    return value


def aggregate_summaries(manifest, root, profile):
    requests = ordered_manifest_requests(manifest)
    plan = shard_plan(manifest, profile)
    totals = {
        "request_count": len(requests),
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "shard_count": plan["shard_count"],
        "completed_shards": 0,
        "failed_shards": 0,
        "production_phase_seconds": 0.0,
        "runner_phase_seconds": 0.0,
        "peak_cpu_percent": 0.0,
        "peak_memory_percent": 0.0,
    }
    for index in range(plan["shard_count"]):
        expected = len(select_shard(requests, index, profile))
        unit = _read_unit(root, index)
        valid = unit is not None
        if valid:
            try:
                counts = tuple(int(unit[key]) for key in ("success", "failed", "skipped"))
                valid = (
                    unit["shard_index"] == index
                    and int(unit["request_count"]) == expected
                    and int(unit["concurrency"]) == plan["concurrency"]
                    and all(value >= 0 for value in counts)
                    and sum(counts) == expected
                )
            except (KeyError, TypeError, ValueError):
                valid = False
        if not valid:
            totals["failed"] += expected
            totals["failed_shards"] += 1
            continue
        totals["success"] += counts[0]
        totals["failed"] += counts[1]
        totals["skipped"] += counts[2]
        totals["completed_shards"] += 1
        if counts[1]:
            totals["failed_shards"] += 1
        duration = float(unit["production_phase_seconds"])
        totals["production_phase_seconds"] = max(
            totals["production_phase_seconds"], duration
        )
        totals["runner_phase_seconds"] += duration
        totals["peak_cpu_percent"] = max(
            totals["peak_cpu_percent"], float(unit["peak_cpu_percent"])
        )
        totals["peak_memory_percent"] = max(
            totals["peak_memory_percent"], float(unit["peak_memory_percent"])
        )
    if totals["success"] + totals["failed"] + totals["skipped"] != len(requests):
        raise AggregateError("Aggregated counts do not cover the canonical batch")
    return totals


def write_diagnostic(summary):
    DIAGNOSTIC.write_text(json.dumps({
        "schema_version": 1,
        "stage": "execute",
        "error_code": E_EXEC,
        "retryable": True,
        "execution_started": True,
        "verification_completed": False,
        "detail": (
            "Isolated execution incomplete: "
            f"failed_items={summary['failed']} failed_units={summary['failed_shards']}"
        ),
    }, sort_keys=True) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="/tmp/runtime-batch.json")
    parser.add_argument("--results", required=True)
    parser.add_argument("--profile", choices=("paired", "single"), required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    summary = aggregate_summaries(manifest, args.results, args.profile)
    PUBLIC_SUMMARY.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    if summary["failed"]:
        write_diagnostic(summary)
    print(
        "Aggregate PASS: items={request_count} failed={failed} units={shard_count}".format(
            **summary
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (AggregateError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        raise SystemExit("E_EXEC_001") from None
