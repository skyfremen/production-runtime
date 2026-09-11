"""Bounded Daily Production orchestration.

Only per-video local generation runs concurrently. Validation/recovery
authorization, upload intent creation, YouTube insertion, durable GitHub writes,
publication verification and receipt creation stay on the coordinator thread.
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

WORKER_ROOT = Path("/tmp/runtime-workers")
FAILURES_PATH = Path("/tmp/batch-failures.txt")
SKIPPED_PATH = Path("/tmp/batch-skipped.txt")
PENDING_PATH = Path("/tmp/batch-pending-verification.txt")
PIPELINE_METRICS_PATH = Path("/tmp/batch-pipeline-metrics.tsv")
WORKER_METRICS_PATH = Path("/tmp/batch-worker-metrics.tsv")
RESOURCE_METRICS_PATH = Path("/tmp/batch-resource-metrics.tsv")
SUMMARY_PATH = Path("/tmp/batch-production-summary.json")
SUPPORTED_CONCURRENCY = (1, 2, 3, 4)


class PipelineError(RuntimeError):
    pass


@dataclass
class PreparedRequest:
    index: int
    request: str
    content_id: str
    env: dict
    worker_dir: Path
    upload_required: bool
    queued_at: float = 0.0


@dataclass
class GenerationResult:
    item: PreparedRequest
    worker_id: str
    worker_started_at: float
    worker_completed_at: float
    stage_seconds: dict
    error: str | None = None


def epoch_seconds():
    return time.time()


def parse_concurrency(raw):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise PipelineError("SHORTS_CONCURRENCY must be an integer from 1 to 4") from None
    if value not in SUPPORTED_CONCURRENCY:
        raise PipelineError("SHORTS_CONCURRENCY must be one of 1, 2, 3, 4")
    return value


def effective_cpu_count():
    """Return CPUs available to this runner/container, not the host total."""
    try:
        available = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        available = 0
    return max(1, int(available or os.cpu_count() or 1))


def threads_per_worker(concurrency, explicit=None, cpu_count=None):
    cpus = max(1, int(cpu_count or effective_cpu_count()))
    if explicit not in (None, ""):
        try:
            threads = int(explicit)
        except (TypeError, ValueError):
            raise PipelineError("SHORTS_THREADS_PER_WORKER must be a positive integer") from None
        if threads < 1:
            raise PipelineError("SHORTS_THREADS_PER_WORKER must be a positive integer")
        return threads
    return max(1, cpus // concurrency)


def _atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _append(path, text):
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(text)


def _read_key_values(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _aggregate_rss_bytes():
    total_kib = 0
    for status in Path("/proc").glob("[0-9]*/status"):
        try:
            for line in status.read_text(errors="ignore").splitlines():
                if line.startswith("VmRSS:"):
                    total_kib += int(line.split()[1])
                    break
        except (OSError, ValueError, IndexError):
            continue
    return total_kib * 1024


def _process_counts():
    ffmpeg = 0
    render = 0
    for command in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            text = command.read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        except OSError:
            continue
        ffmpeg += "ffmpeg" in text
        render += "render_aligned.py" in text
    return int(ffmpeg), int(render)


def _cpu_snapshot():
    fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
    values = [int(value) for value in fields]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def _memory_total_bytes():
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    return 0


class ResourceSampler:
    def __init__(self, path=RESOURCE_METRICS_PATH, interval=2.0):
        self.path = Path(path)
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = None
        self.peak_rss_bytes = 0
        self.peak_cpu_percent = 0.0
        self.peak_load_1m = 0.0
        self.memory_total_bytes = _memory_total_bytes()

    def start(self):
        self.path.write_text(
            "epoch_seconds\tcpu_percent\tload_1m\taggregate_rss_bytes\tffmpeg_processes\trender_processes\n",
            encoding="utf-8",
        )
        self.thread = threading.Thread(target=self._sample, name="resource-sampler", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=max(2.0, self.interval * 2))

    def _sample(self):
        previous_total, previous_idle = _cpu_snapshot()
        while not self.stop_event.wait(self.interval):
            try:
                current_total, current_idle = _cpu_snapshot()
                delta_total = max(1, current_total - previous_total)
                cpu = 100.0 * (1.0 - (current_idle - previous_idle) / delta_total)
                previous_total, previous_idle = current_total, current_idle
                load = os.getloadavg()[0]
                rss = _aggregate_rss_bytes()
                ffmpeg, render = _process_counts()
                self.peak_cpu_percent = max(self.peak_cpu_percent, cpu)
                self.peak_load_1m = max(self.peak_load_1m, load)
                self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
                _append(
                    self.path,
                    f"{epoch_seconds():.6f}\t{cpu:.3f}\t{load:.3f}\t{rss}\t{ffmpeg}\t{render}\n",
                )
            except (OSError, ValueError):
                continue


class ProductionPipeline:
    def __init__(self, concurrency, *, worker_root=WORKER_ROOT, base_env=None, sampler=None):
        self.concurrency = parse_concurrency(concurrency)
        self.cpu_count = effective_cpu_count()
        self.base_env = dict(base_env or os.environ)
        self.worker_threads = threads_per_worker(
            self.concurrency,
            self.base_env.get("SHORTS_THREADS_PER_WORKER"),
            self.cpu_count,
        )
        self.worker_root = Path(worker_root)
        self.sampler = sampler or ResourceSampler()
        self.failed = 0
        self.skipped = 0
        self.pending = 0
        self.generation_results = []

    def initialize(self):
        if self.worker_root == Path("/") or len(self.worker_root.parts) < 3:
            raise PipelineError("Refusing unsafe worker root")
        shutil.rmtree(self.worker_root, ignore_errors=True)
        self.worker_root.mkdir(parents=True)
        FAILURES_PATH.write_text("", encoding="utf-8")
        SKIPPED_PATH.write_text("", encoding="utf-8")
        PENDING_PATH.write_text("", encoding="utf-8")
        PIPELINE_METRICS_PATH.write_text(
            "content_id\tevent\tepoch_seconds\n", encoding="utf-8"
        )
        WORKER_METRICS_PATH.write_text(
            "content_id\tworker_id\tqueue_wait_seconds\tworker_seconds\tmedia_seconds\trender_seconds\tverify_seconds\tupload_seconds\tstatus\n",
            encoding="utf-8",
        )

    def source_for_request(self, request):
        mapping = json.loads(self.base_env.get("REQUEST_SOURCE_MAP_JSON", "{}"))
        entry = mapping.get(str(request), {})
        return str(entry.get("source_commit_sha", ""))

    def environment_for(self, request):
        content_id = Path(request).stem
        env = dict(self.base_env)
        env.update({
            "SOURCE_COMMIT_SHA": self.source_for_request(request),
            "SOURCE_REQUEST_BLOB_SHA": str(
                json.loads(self.base_env.get("REQUEST_SOURCE_MAP_JSON", "{}"))
                .get(str(request), {})
                .get("request_blob_sha", "")
            ),
            "STORY_OUTPUT_DIR": f"runtime/output/{content_id}",
            "OMP_NUM_THREADS": str(self.worker_threads),
            "MKL_NUM_THREADS": str(self.worker_threads),
            "OPENBLAS_NUM_THREADS": str(self.worker_threads),
            "NUMEXPR_NUM_THREADS": str(self.worker_threads),
            "TORCH_NUM_THREADS": str(self.worker_threads),
            "TORCH_INTEROP_THREADS": "1",
            "KOKORO_ONNX_INTRA_OP_THREADS": str(self.worker_threads),
            "KOKORO_ONNX_INTER_OP_THREADS": "1",
            "FFMPEG_THREADS": str(self.worker_threads),
        })
        return env

    @staticmethod
    def run_command(command, env, log_path):
        started = time.monotonic()
        with Path(log_path).open("a", encoding="utf-8") as log:
            log.write("$ " + " ".join(command) + "\n")
            completed = subprocess.run(
                command, env=env, text=True, stdout=log, stderr=subprocess.STDOUT
            )
        elapsed = time.monotonic() - started
        if completed.returncode:
            label = Path(command[1]).name if len(command) > 1 else command[0]
            raise PipelineError(f"{label} exited {completed.returncode}")
        return elapsed

    def prepare(self, index, request):
        content_id = Path(request).stem
        worker_dir = self.worker_root / f"{index:02d}-{content_id}"
        worker_dir.mkdir(parents=True)
        log_path = worker_dir / "prepare.log"
        env = self.environment_for(request)
        output_dir = Path(env["STORY_OUTPUT_DIR"])
        shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True)
        self.run_command(
            ["python", "runtime/guard/schema.py", "--request", request],
            env,
            log_path,
        )
        with tempfile.NamedTemporaryFile(prefix="runtime-prepare-", delete=False) as output:
            status_path = Path(output.name)
        try:
            prepare_env = dict(env)
            prepare_env["GITHUB_OUTPUT"] = str(status_path)
            self.run_command(
                [
                    "python",
                    "runtime/output/execute.py",
                    "--stage",
                    "prepare",
                    "--request",
                    request,
                ],
                prepare_env,
                log_path,
            )
            status = _read_key_values(status_path)
        finally:
            status_path.unlink(missing_ok=True)
        if status.get("schedule_skipped") == "true":
            reason = status.get("schedule_skip_reason", "schedule guard")
            _append(SKIPPED_PATH, f"{request} | {reason}\n")
            self.skipped += 1
            print(f"::warning::Skipped fresh generation: {request} — {reason}")
            return None
        item = PreparedRequest(
            index=index,
            request=request,
            content_id=content_id,
            env=env,
            worker_dir=worker_dir,
            upload_required=status.get("upload_required") == "true",
        )
        if not item.upload_required:
            self.defer_verification(item)
            return None
        return item

    def generate(self, item):
        worker_id = threading.current_thread().name
        started = epoch_seconds()
        stages = {}
        error = None
        log_path = item.worker_dir / "generation.log"
        try:
            validation = (
                "import json,sys; from pathlib import Path; "
                "sys.path.insert(0,'runtime'); "
                "from resources.validate import load_registry,validate_request_backgrounds; "
                "p=sys.argv[1]; validate_request_backgrounds(json.loads(Path(p).read_text()),load_registry())"
            )
            stages["background_contract"] = self.run_command(
                ["python", "-c", validation, item.request], item.env, log_path
            )
            stages["media"] = self.run_command(
                ["python", "runtime/resources/resolve.py", "--request", item.request],
                item.env,
                log_path,
            )
            stages["render"] = self.run_command(
                ["python", "runtime/transform/process.py", "--request", item.request],
                item.env,
                log_path,
            )
            stages["verify"] = self.run_command(
                ["python", "runtime/transform/verify.py", "--request", item.request],
                item.env,
                log_path,
            )
        except Exception as exc:
            error = str(exc)
        completed = epoch_seconds()
        result = GenerationResult(item, worker_id, started, completed, stages, error)
        _atomic_json(item.worker_dir / "result.json", {
            "content_id": item.content_id,
            "request": item.request,
            "worker_id": worker_id,
            "queued_at": item.queued_at,
            "worker_started_at": started,
            "worker_completed_at": completed,
            "queue_wait_seconds": max(0.0, started - item.queued_at),
            "stage_seconds": stages,
            "status": "failed" if error else "generated",
            "error": error,
        })
        return result

    def upload(self, result):
        item = result.item
        if result.error:
            raise PipelineError(result.error)
        upload_started = epoch_seconds()
        upload_seconds = self.run_command(
            [
                "python",
                "runtime/output/execute.py",
                "--stage",
                "upload",
                "--request",
                item.request,
            ],
            item.env,
            item.worker_dir / "upload.log",
        )
        upload_completed = epoch_seconds()
        result_path = item.worker_dir / "result.json"
        evidence = json.loads(result_path.read_text(encoding="utf-8"))
        evidence.update({
            "upload_started_at": upload_started,
            "upload_completed_at": upload_completed,
            "upload_seconds": upload_seconds,
            "status": "uploaded",
        })
        _atomic_json(result_path, evidence)
        result.stage_seconds["upload"] = upload_seconds
        self.defer_verification(item)

    def defer_verification(self, item):
        queued = epoch_seconds()
        _append(PENDING_PATH, f"{item.request}\t{queued:.6f}\n")
        _append(
            PIPELINE_METRICS_PATH,
            f"{item.content_id}\tverification_deferred\t{queued:.6f}\n",
        )
        self.pending += 1

    def record_generation(self, result):
        self.generation_results.append(result)
        item = result.item
        queue_wait = max(0.0, result.worker_started_at - item.queued_at)
        worker_seconds = result.worker_completed_at - result.worker_started_at
        result_path = item.worker_dir / "result.json"
        if result_path.exists():
            status = json.loads(result_path.read_text(encoding="utf-8")).get("status", "generated")
        else:
            status = "failed" if result.error else "generated"
        _append(
            WORKER_METRICS_PATH,
            f"{item.content_id}\t{result.worker_id}\t{queue_wait:.6f}\t{worker_seconds:.6f}\t"
            f"{result.stage_seconds.get('media', 0.0):.6f}\t{result.stage_seconds.get('render', 0.0):.6f}\t"
            f"{result.stage_seconds.get('verify', 0.0):.6f}\t"
            f"{result.stage_seconds.get('upload', 0.0):.6f}\t{status}\n",
        )
        log_path = item.worker_dir / "generation.log"
        if log_path.exists():
            print(log_path.read_text(encoding="utf-8", errors="replace"), end="")

    def record_failure(self, request, stage, exc):
        self.failed += 1
        _append(FAILURES_PATH, f"{request} | {stage} failure | {exc}\n")
        print(
            f"::error title=Per-video {stage} failure::Failed: {request} — "
            "continuing remaining Shorts; durable intent/recovery rules remain authoritative"
        )

    def run(self, requests):
        self.initialize()
        batch_started = epoch_seconds()
        _append(PIPELINE_METRICS_PATH, f"-\tproduction_started\t{batch_started:.6f}\n")
        print(
            f"Bounded production pipeline: concurrency={self.concurrency} logical_cpus={self.cpu_count} "
            f"threads_per_worker={self.worker_threads}"
        )
        self.sampler.start()
        active = []
        try:
            with ThreadPoolExecutor(
                max_workers=self.concurrency, thread_name_prefix="short-worker"
            ) as executor:
                for index, request in enumerate(requests):
                    try:
                        item = self.prepare(index, request)
                    except Exception as exc:
                        message = str(exc)
                        stage = (
                            "validation" if "validate_content.py" in message
                            else "authorization" if "publish.py" in message
                            else "prepare"
                        )
                        self.record_failure(request, stage, exc)
                        continue
                    if item is None:
                        continue
                    item.queued_at = epoch_seconds()
                    active.append((item, executor.submit(self.generate, item)))
                    if len(active) >= self.concurrency:
                        self._finish_oldest(active)
                while active:
                    self._finish_oldest(active)
        finally:
            self.sampler.stop()
        completed = epoch_seconds()
        summary = {
            "production_started_at": batch_started,
            "concurrency": self.concurrency,
            "logical_cpu_count": self.cpu_count,
            "threads_per_worker": self.worker_threads,
            "request_count": len(requests),
            "pending_verification": self.pending,
            "skipped": self.skipped,
            "failed": self.failed,
            "production_phase_seconds": completed - batch_started,
            "peak_cpu_percent": self.sampler.peak_cpu_percent,
            "peak_load_1m": self.sampler.peak_load_1m,
            "peak_aggregate_rss_bytes": self.sampler.peak_rss_bytes,
            "memory_total_bytes": self.sampler.memory_total_bytes,
            "peak_memory_percent": (
                100.0 * self.sampler.peak_rss_bytes / self.sampler.memory_total_bytes
                if self.sampler.memory_total_bytes
                else 0.0
            ),
        }
        _atomic_json(SUMMARY_PATH, summary)
        print("PERF_METRIC " + " ".join(
            f"{key}={value:.3f}" if isinstance(value, float) else f"{key}={value}"
            for key, value in summary.items()
            if key not in {"request_count", "pending_verification", "skipped", "failed"}
        ))
        return summary

    def _finish_oldest(self, active):
        item, future = active.pop(0)
        result = future.result()
        try:
            self.upload(result)
            self.record_generation(result)
            print(f"Queued for exact YouTube verification: {item.request}")
        except Exception as exc:
            if not result.error:
                result_path = item.worker_dir / "result.json"
                if result_path.exists():
                    evidence = json.loads(result_path.read_text(encoding="utf-8"))
                    evidence.update({"status": "upload_failed", "error": str(exc)})
                    _atomic_json(result_path, evidence)
            self.record_generation(result)
            stage = "generation" if result.error else "upload"
            self.record_failure(item.request, stage, exc)


def read_requests(path):
    requests = [
        line.strip()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not 1 <= len(requests) <= 24:
        raise PipelineError(f"Production request list must contain 1-24 requests; got {len(requests)}")
    content_ids = [Path(path).stem for path in requests]
    duplicates = sorted({value for value in content_ids if content_ids.count(value) > 1})
    if duplicates:
        raise PipelineError("Duplicate content_id in production queue: " + ", ".join(duplicates))
    return requests


def main():
    parser = argparse.ArgumentParser(description="Run bounded workers")
    parser.add_argument("--request-list", default="/tmp/batch-requests.txt")
    parser.add_argument("--concurrency", default=os.getenv("SHORTS_CONCURRENCY", "1"))
    args = parser.parse_args()
    try:
        requests = read_requests(args.request_list)
        ProductionPipeline(args.concurrency).run(requests)
    except (OSError, PipelineError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
