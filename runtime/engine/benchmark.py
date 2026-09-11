"""On-demand, upload-free benchmark for the production render path.

This module is intentionally not wired to a recurring workflow. It exercises
real media resolution/download, Kokoro TTS, Wav2Vec2 alignment, the canonical
1080x1920 FFmpeg render and verify_render without touching external publication or durable
GitHub publication state.
"""
import argparse
import json
import os
import shutil
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engine.check import synthetic_request
from engine.pipeline import (
    GenerationResult,
    PreparedRequest,
    ProductionPipeline,
    ResourceSampler,
    threads_per_worker,
)

BASE = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("/tmp/concurrency-benchmark.json")


def representative_script(index):
    sentences = [
        "My manager said the missing record proved I had ignored the request.",
        "I remembered saving every message because the instructions kept changing.",
        "The first folder looked empty, and the meeting became uncomfortably quiet.",
        "Then I opened the archived copy and found a timestamp nobody expected.",
        "It showed the file had been moved after I completed the work.",
        "The audit trail also showed exactly who changed the access permissions.",
        "I shared the screen, checked the dates twice, and let everyone read it.",
        "Nobody interrupted because the evidence answered every accusation.",
        "The manager finally admitted the process had failed, not my work.",
        "By the end of the meeting, the missing record was restored.",
        "The team added a review step so the same mistake could not happen again.",
        "I kept the backup, but I never needed to defend that story again.",
    ]
    # Roughly 520 words: representative of the current 120-175 second target at
    # the frozen story-aware 1.75x narration speed without changing render settings.
    rotated = sentences[index % len(sentences):] + sentences[:index % len(sentences)]
    return " ".join(rotated * 4)


def build_requests(root, count):
    request_dir = Path(root) / "requests"
    request_dir.mkdir(parents=True)
    requests = []
    for index in range(count):
        data = synthetic_request(index)
        data["story"]["script"] = representative_script(index)
        data["planning"]["target_duration_seconds"] = 145
        path = request_dir / f"{data['content_id']}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        requests.append(str(path))
    return requests


def semantic_signature(output_dir):
    metadata = json.loads((output_dir / "render-metadata.json").read_text(encoding="utf-8"))
    selection = json.loads((output_dir / "background_selection.json").read_text(encoding="utf-8"))
    return {
        "content_id": metadata["content_id"],
        "resolution": metadata["resolution"],
        "fps": metadata["fps"],
        "narration_backend_requested": metadata["narration_backend_requested"],
        "narration_backend_used": metadata["narration_backend_used"],
        "narration_voice": metadata["narration_voice"],
        "narration_speed": metadata["narration_speed"],
        "x264_preset": metadata["x264_preset"],
        "x264_crf": metadata["x264_crf"],
        "caption_timing_mode": metadata.get("caption_timing_mode"),
        "caption_alignment_backend": metadata.get("caption_alignment_backend"),
        "render_verified": metadata.get("render_verified"),
        "background_asset_id": selection["background_asset_id"],
        "background_selection": selection["background_selection"],
    }


def run_candidate(concurrency, requests, root, *, explicit_threads=None):
    candidate_root = Path(root) / f"c{concurrency}"
    shutil.rmtree(candidate_root, ignore_errors=True)
    candidate_root.mkdir(parents=True)
    env = dict(os.environ)
    if explicit_threads:
        env["SHORTS_THREADS_PER_WORKER"] = str(explicit_threads)
    pipeline = ProductionPipeline(
        concurrency,
        worker_root=candidate_root / "workers",
        base_env=env,
        sampler=ResourceSampler(candidate_root / "resources.tsv", interval=2.0),
    )
    pipeline.worker_root.mkdir(parents=True)
    items = []
    for index, request in enumerate(requests):
        content_id = Path(request).stem
        worker_dir = pipeline.worker_root / f"{index:02d}-{content_id}"
        worker_dir.mkdir(parents=True)
        item_env = pipeline.environment_for(request)
        output_dir = candidate_root / "output" / content_id
        item_env["STORY_OUTPUT_DIR"] = str(output_dir)
        items.append(
            PreparedRequest(index, request, content_id, item_env, worker_dir, True, time.time())
        )

    started = time.monotonic()
    pipeline.sampler.start()
    try:
        with ThreadPoolExecutor(
            max_workers=concurrency, thread_name_prefix="benchmark-worker"
        ) as executor:
            futures = [executor.submit(pipeline.generate, item) for item in items]
            results = [future.result() for future in futures]
    finally:
        pipeline.sampler.stop()
    elapsed = time.monotonic() - started
    failures = [result for result in results if result.error]
    if failures:
        for result in failures:
            log_path = result.item.worker_dir / "generation.log"
            if log_path.exists():
                print(log_path.read_text(encoding="utf-8", errors="replace"))
        detail = "; ".join(
            f"{result.item.content_id}: {result.error}" for result in failures
        )
        raise RuntimeError(f"Concurrency {concurrency} benchmark failed: {detail}")

    signatures = [
        semantic_signature(candidate_root / "output" / result.item.content_id)
        for result in results
    ]
    render_metadata = [
        json.loads(
            (candidate_root / "output" / result.item.content_id / "render-metadata.json").read_text(
                encoding="utf-8"
            )
        )
        for result in results
    ]
    worker_seconds = [
        result.worker_completed_at - result.worker_started_at for result in results
    ]
    return {
        "concurrency": concurrency,
        "threads_per_worker": pipeline.worker_threads,
        "logical_cpu_count": pipeline.cpu_count,
        "batch_seconds": elapsed,
        "billable_minute_proxy": int((elapsed + 59.999999) // 60),
        "median_short_seconds": statistics.median(worker_seconds),
        "median_ffmpeg_seconds": statistics.median(
            value["ffmpeg_duration_seconds"] for value in render_metadata
        ),
        "median_tts_seconds": statistics.median(
            value["tts_generation_duration_seconds"] for value in render_metadata
        ),
        "median_alignment_seconds": statistics.median(
            value.get("caption_alignment_duration_seconds", 0.0) for value in render_metadata
        ),
        "peak_cpu_percent": pipeline.sampler.peak_cpu_percent,
        "peak_load_1m": pipeline.sampler.peak_load_1m,
        "peak_aggregate_rss_bytes": pipeline.sampler.peak_rss_bytes,
        "memory_total_bytes": pipeline.sampler.memory_total_bytes,
        "peak_memory_percent": (
            100.0 * pipeline.sampler.peak_rss_bytes / pipeline.sampler.memory_total_bytes
            if pipeline.sampler.memory_total_bytes
            else 0.0
        ),
        "failures": 0,
        "signatures": signatures,
    }


def equivalent_to_baseline(baseline, candidate):
    return baseline["signatures"] == candidate["signatures"]


def choose_winner(results):
    valid = [result for result in results if result["equivalent"] and result["failures"] == 0]
    best_seconds = min(result["batch_seconds"] for result in valid)
    # Prefer the lowest level within 3% of the fastest measured candidate.
    return min(
        result["concurrency"]
        for result in valid
        if result["batch_seconds"] <= best_seconds * 1.03
    )


def run_stage_a(requests, root):
    results = []
    previous = None
    baseline = None
    for concurrency in (1, 2, 3, 4):
        current = run_candidate(concurrency, requests, root)
        if baseline is None:
            baseline = current
        current["equivalent"] = equivalent_to_baseline(baseline, current)
        results.append(current)
        if not current["equivalent"]:
            break
        if current["peak_memory_percent"] >= 85.0:
            break
        if previous is not None:
            incremental = (previous["batch_seconds"] - current["batch_seconds"]) / previous["batch_seconds"]
            current["incremental_reduction_vs_previous"] = incremental
            if incremental <= 0.02:
                break
        previous = current
    return results, choose_winner(results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("a", "b"), required=True)
    parser.add_argument("--winner", type=int)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    count = 4 if args.stage == "a" else 24
    if args.stage == "b" and args.winner not in (2, 3, 4):
        raise SystemExit("Stage B requires --winner 2, 3 or 4")

    with tempfile.TemporaryDirectory(prefix="runtime-concurrency-benchmark-") as holder:
        root = Path(holder)
        requests = build_requests(root, count)
        if args.stage == "a":
            results, winner = run_stage_a(requests, root)
        else:
            levels = (1, args.winner)
            results = []
            baseline = None
            for concurrency in levels:
                current = run_candidate(concurrency, requests, root)
                baseline = baseline or current
                current["equivalent"] = equivalent_to_baseline(baseline, current)
                results.append(current)
            winner = choose_winner(results)

        baseline_seconds = results[0]["batch_seconds"]
        for result in results:
            saved = baseline_seconds - result["batch_seconds"]
            result["seconds_saved_vs_concurrency_1"] = saved
            result["percent_reduction_vs_concurrency_1"] = (
                100.0 * saved / baseline_seconds if baseline_seconds else 0.0
            )
        report = {
            "stage": args.stage,
            "request_count": count,
            "production_side_effects": False,
            "winner": winner,
            "results": results,
        }
        Path(args.output).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
