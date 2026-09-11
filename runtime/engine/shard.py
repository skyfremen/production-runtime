"""Deterministic bounded request partitioning for isolated execution units."""
import argparse
import json
import os
from pathlib import Path

from engine.batch import BatchError, ordered_manifest_requests

MAX_SHARDS = 12
SUPPORTED_SHARD_SIZE = 2


class ShardError(RuntimeError):
    pass


def partition_requests(requests, shard_size=SUPPORTED_SHARD_SIZE):
    ordered = tuple(requests)
    if shard_size != SUPPORTED_SHARD_SIZE:
        raise ShardError("Unsupported shard size")
    if not 1 <= len(ordered) <= 24:
        raise ShardError("Request count must be between 1 and 24")
    shards = tuple(
        ordered[index:index + shard_size]
        for index in range(0, len(ordered), shard_size)
    )
    if len(shards) > MAX_SHARDS:
        raise ShardError("Shard count exceeds bounded execution capacity")
    flattened = tuple(item for shard in shards for item in shard)
    if flattened != ordered or len(flattened) != len(set(flattened)):
        raise ShardError("Shard assignment is not one-to-one and deterministic")
    return shards


def shard_plan(manifest, profile):
    ordered = ordered_manifest_requests(manifest)
    if profile == "single":
        if len(ordered) != 1:
            raise ShardError("Single profile requires exactly one request")
        shards = (ordered,)
        concurrency = 1
    elif profile == "paired":
        shards = partition_requests(ordered)
        concurrency = 2
    else:
        raise ShardError("Unsupported execution profile")
    return {
        "request_count": len(ordered),
        "shard_count": len(shards),
        "concurrency": concurrency,
        "matrix": {"include": [{"index": index} for index in range(len(shards))]},
    }


def select_shard(requests, index, profile):
    if profile == "single":
        if index != 0 or len(requests) != 1:
            raise ShardError("Invalid single execution assignment")
        return tuple(requests)
    shards = partition_requests(requests)
    if not 0 <= index < len(shards):
        raise ShardError("Shard index is outside the deterministic plan")
    return shards[index]


def write_outputs(plan):
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as target:
        target.write("matrix=" + json.dumps(plan["matrix"], separators=(",", ":")) + "\n")
        target.write(f"request_count={plan['request_count']}\n")
        target.write(f"shard_count={plan['shard_count']}\n")
        target.write(f"concurrency={plan['concurrency']}\n")


def main():
    parser = argparse.ArgumentParser(description="Build a bounded execution plan")
    parser.add_argument("--manifest", default="/tmp/runtime-batch.json")
    parser.add_argument("--profile", choices=("paired", "single"), required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    plan = shard_plan(manifest, args.profile)
    write_outputs(plan)
    print(
        f"Plan PASS: items={plan['request_count']} units={plan['shard_count']}"
    )


if __name__ == "__main__":
    try:
        main()
    except (BatchError, ShardError, KeyError, OSError, ValueError, json.JSONDecodeError):
        raise SystemExit("E_PLAN_001") from None
