"""Sparse per-video audience-retention checkpoint collection."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VIDEO_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHECKPOINT_WINDOWS = {
    "72h": (60.0, 120.0),
    "7d": (144.0, 216.0),
}
RETENTION_METRICS = "audienceWatchRatio,relativeRetentionPerformance,startedWatching,stoppedWatching,totalSegmentImpressions"
CORE_RETENTION_METRICS = "audienceWatchRatio,relativeRetentionPerformance"
EARLIEST_RETENTION_DATE = "2008-07-01"


@dataclass
class RetentionResult:
    files: list[Path]
    warnings: list[str]


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def due_retention_checkpoints(videos: list[dict], completed: dict) -> list[tuple[str, str, float]]:
    due = []
    for item in videos:
        video_id = str(item.get("youtube_video_id") or "")
        try:
            age = float(item.get("age_hours"))
        except (TypeError, ValueError):
            continue
        if not VIDEO_RE.fullmatch(video_id):
            continue
        done = completed.get(video_id) if isinstance(completed.get(video_id), dict) else {}
        for checkpoint, (minimum, maximum) in CHECKPOINT_WINDOWS.items():
            if minimum <= age <= maximum and checkpoint not in done:
                due.append((video_id, checkpoint, age))
                break
    return due


def collect_retention(
    videos: list[dict],
    collection_state: dict,
    warehouse_root: Path,
    query,
    collected_at: datetime,
    max_queries: int = 24,
) -> RetentionResult:
    files: list[Path] = []
    warnings: list[str] = []
    completed = collection_state.setdefault("retention_checkpoints", {})
    attempts = collection_state.setdefault("retention_attempts", {})
    captured_at = iso_z(collected_at)
    start_dates: dict[str, str] = {}
    for item in videos:
        video_id = str(item.get("youtube_video_id") or "")
        raw = str(item.get("publish_at") or "")
        try:
            published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            start_dates[video_id] = published.date().isoformat()
        except (TypeError, ValueError):
            start_dates[video_id] = EARLIEST_RETENTION_DATE
    due = due_retention_checkpoints(videos, completed)
    due.sort(key=lambda item: (
        int((attempts.get(f"{item[0]}/{item[1]}") or {}).get("attempts") or 0),
        str((attempts.get(f"{item[0]}/{item[1]}") or {}).get("last_attempted_at") or ""),
        abs(item[2] - (72.0 if item[1] == "72h" else 168.0)),
        item[0],
    ))
    empty_count = 0
    error_types: dict[str, int] = {}
    for video_id, checkpoint, age in due[:max(0, int(max_queries))]:
        attempt_key = f"{video_id}/{checkpoint}"
        previous = attempts.get(attempt_key) if isinstance(attempts.get(attempt_key), dict) else {}
        attempts[attempt_key] = {
            "attempts": int(previous.get("attempts") or 0) + 1,
            "last_attempted_at": captured_at,
            "observed_age_hours": round(age, 2),
        }
        try:
            params = {
                "ids": "channel==MINE",
                "startDate": start_dates.get(video_id, EARLIEST_RETENTION_DATE),
                "endDate": collected_at.date().isoformat(),
                "metrics": RETENTION_METRICS,
                "dimensions": "elapsedVideoTimeRatio",
                "filters": f"video=={video_id}",
            }
            report = query(params)
            if not (report.get("rows") or []):
                report = query({**params, "metrics": CORE_RETENTION_METRICS})
            if not (report.get("rows") or []):
                empty_count += 1
                continue
            path = warehouse_root / "retention" / video_id / f"{checkpoint}.json"
            payload = {
                "analytics_version": 3,
                "captured_at": captured_at,
                "checkpoint": checkpoint,
                "observed_age_hours": round(age, 2),
                "source": "YouTube Analytics API v2",
                "video_id": video_id,
                "column_headers": report.get("columnHeaders", []),
                "rows": report.get("rows", []) or [],
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            completed.setdefault(video_id, {})[checkpoint] = {
                "captured_at": captured_at,
                "observed_age_hours": round(age, 2),
                "path": path.relative_to(warehouse_root).as_posix(),
            }
            files.append(path)
        except Exception as exc:
            name = type(exc).__name__
            error_types[name] = error_types.get(name, 0) + 1
    if empty_count:
        warnings.append(f"Optional retention returned empty curves for {empty_count} due checkpoints; will retry")
    for name, count in sorted(error_types.items()):
        warnings.append(f"Optional retention unavailable for {count} due checkpoints ({name}); will retry")
    return RetentionResult(files, warnings)
