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
) -> RetentionResult:
    files: list[Path] = []
    warnings: list[str] = []
    completed = collection_state.setdefault("retention_checkpoints", {})
    captured_at = iso_z(collected_at)
    for video_id, checkpoint, age in due_retention_checkpoints(videos, completed):
        try:
            report = query({
                "ids": "channel==MINE",
                "startDate": "2000-01-01",
                "endDate": collected_at.date().isoformat(),
                "metrics": RETENTION_METRICS,
                "dimensions": "elapsedVideoTimeRatio",
                "filters": f"video=={video_id}",
            })
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
            warnings.append(f"Optional retention {video_id}/{checkpoint} unavailable: {type(exc).__name__}")
    return RetentionResult(files, warnings)
