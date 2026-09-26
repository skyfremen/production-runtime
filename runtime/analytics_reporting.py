"""Best-effort YouTube Reporting API discovery and immutable report ingestion."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


@dataclass
class ReportingSyncResult:
    files: list[Path]
    warnings: list[str]


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def relevant_report_type(item: dict) -> bool:
    report_type_id = str(item.get("id") or "")
    return report_type_id.startswith(("channel_", "playlist_")) and not item.get("deprecateTime")


def report_category(report_type_id: str) -> str:
    value = report_type_id.casefold()
    if value.startswith("playlist_"):
        return "playlists"
    for needle, category in (
        ("traffic_source", "traffic-source"),
        ("playback_location", "playback-location"),
        ("device_os", "device-os"),
        ("demographics", "demographics"),
        ("sharing", "sharing"),
        ("subtitles", "subtitles"),
        ("end_screen", "end-screens"),
        ("cards", "cards"),
        ("combined", "combined"),
        ("reach_basic", "reach-basic"),
        ("reach_combined", "reach-combined"),
        ("basic", "basic"),
    ):
        if needle in value:
            return category
    return "other-supported-report-types"


def paged(api_json, path: str, key: str) -> list[dict]:
    rows = []
    next_path = path
    while next_path:
        response = api_json("GET", next_path, None)
        rows.extend(item for item in response.get(key, []) if isinstance(item, dict))
        token = str(response.get("nextPageToken") or "")
        next_path = f"{path}&pageToken={quote(token)}" if token else ""
    return rows


def sync_reporting(
    token: str,
    warehouse_root: Path,
    jobs_manifest: dict,
    collection_state: dict,
    api_json,
    api_bytes,
    collected_at: datetime,
) -> ReportingSyncResult:
    del token  # Authentication is owned by the injected HTTP transport.
    files: list[Path] = []
    warnings: list[str] = []
    timestamp = iso_z(collected_at)
    jobs_manifest.setdefault("analytics_version", 3)
    jobs_manifest.setdefault("jobs", {})
    jobs_manifest.setdefault("report_types", {})
    collection_state.setdefault("downloaded_reports", {})

    try:
        discovered = paged(api_json, "reportTypes?pageSize=100", "reportTypes")
    except Exception as exc:
        warnings.append(f"Reporting API discovery unavailable: {type(exc).__name__}")
        return ReportingSyncResult(files, warnings)

    report_types = {str(item["id"]): item for item in discovered if relevant_report_type(item)}
    jobs_manifest["report_types"] = {
        report_type_id: {
            key: item[key]
            for key in ("id", "name", "systemManaged", "deprecateTime")
            if key in item
        }
        for report_type_id, item in sorted(report_types.items())
    }

    try:
        live_jobs = paged(api_json, "jobs?pageSize=100", "jobs")
    except Exception as exc:
        live_jobs = []
        warnings.append(f"Reporting API jobs list unavailable: {type(exc).__name__}")
    by_type = {
        str(item.get("reportTypeId")): item
        for item in live_jobs
        if item.get("id") and item.get("reportTypeId") in report_types
    }
    for report_type_id, item in list(jobs_manifest["jobs"].items()):
        if report_type_id in report_types and isinstance(item, dict) and item.get("id"):
            by_type.setdefault(report_type_id, item)

    for report_type_id, report_type in sorted(report_types.items()):
        if report_type_id in by_type:
            continue
        if report_type.get("systemManaged") is True:
            warnings.append(f"System-managed report {report_type_id} has no listed job yet")
            continue
        try:
            created = api_json(
                "POST",
                "jobs",
                {"reportTypeId": report_type_id, "name": f"wacky-analytics-{report_type_id}"},
            )
            if created.get("id"):
                by_type[report_type_id] = created
        except Exception as exc:
            warnings.append(f"Optional Reporting job {report_type_id} unavailable: {type(exc).__name__}")

    jobs_manifest["jobs"] = {key: by_type[key] for key in sorted(by_type)}
    jobs_manifest["updated_at"] = timestamp

    for report_type_id, job in sorted(by_type.items()):
        job_id = str(job.get("id") or "")
        if not job_id:
            continue
        try:
            reports = paged(api_json, f"jobs/{quote(job_id)}/reports?pageSize=100", "reports")
        except Exception as exc:
            warnings.append(f"Optional Reporting reports for {job_id} unavailable: {type(exc).__name__}")
            continue
        for report in reports:
            report_id = str(report.get("id") or "")
            download_url = str(report.get("downloadUrl") or "")
            if not report_id or not download_url or report_id in collection_state["downloaded_reports"]:
                continue
            try:
                payload = api_bytes(download_url)
                if not isinstance(payload, bytes):
                    raise TypeError("report download was not bytes")
                base = warehouse_root / "raw" / report_category(report_type_id) / report_type_id
                csv_path = base / f"{report_id}.csv"
                metadata_path = base / f"{report_id}.metadata.json"
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                csv_path.write_bytes(payload)
                metadata = {
                    "analytics_version": 3,
                    "authenticated_source": "channel==MINE",
                    "job_id": job_id,
                    "report_id": report_id,
                    "report_type_id": report_type_id,
                    "retrieved_at": timestamp,
                    **{key: report[key] for key in ("startTime", "endTime", "createTime", "jobExpireTime") if key in report},
                }
                write_json(metadata_path, metadata)
                rel = csv_path.relative_to(warehouse_root).as_posix()
                collection_state["downloaded_reports"][report_id] = {
                    "path": rel,
                    "report_type_id": report_type_id,
                    "retrieved_at": timestamp,
                }
                files.extend((csv_path, metadata_path))
            except Exception as exc:
                warnings.append(f"Optional Reporting download {report_id} unavailable: {type(exc).__name__}")

    return ReportingSyncResult(files, warnings)
