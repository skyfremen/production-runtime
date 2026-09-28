"""Best-effort YouTube Reporting API discovery and immutable report ingestion."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote


@dataclass
class ReportingSyncResult:
    files: list[Path]
    warnings: list[str]


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _instant(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def update_job_status(
    report_type_id: str,
    job: dict,
    reports: list[dict],
    statuses: dict,
    collected_at: datetime,
) -> dict:
    checked_at = collected_at.astimezone(timezone.utc)
    previous = statuses.get(report_type_id)
    previous = previous if isinstance(previous, dict) else {}
    candidates = [
        value
        for value in (_instant(previous.get("first_observed_at")), _instant(job.get("createTime")))
        if value is not None
    ]
    first_observed = min(candidates) if candidates else checked_at
    valid_reports = [report for report in reports if isinstance(report, dict)]
    end_times = [str(report.get("endTime")) for report in valid_reports if _instant(report.get("endTime"))]
    status = "active" if valid_reports else (
        "stale" if checked_at - first_observed >= timedelta(hours=48) else "waiting_for_first_report"
    )
    result = {
        "status": status,
        "first_observed_at": iso_z(first_observed),
        "last_checked_at": iso_z(checked_at),
        "available_report_count": len(valid_reports),
        "latest_report_end_time": max(end_times) if end_times else None,
    }
    first_report = previous.get("first_report_observed_at")
    if valid_reports:
        result["first_report_observed_at"] = first_report or iso_z(checked_at)
    elif first_report:
        result["first_report_observed_at"] = first_report
    statuses[report_type_id] = result
    return result


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


def error_label(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code} {exc.reason}".strip()
    return type(exc).__name__


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
    max_downloads: int = 100,
) -> ReportingSyncResult:
    del token  # Authentication is owned by the injected HTTP transport.
    files: list[Path] = []
    warnings: list[str] = []
    timestamp = iso_z(collected_at)
    jobs_manifest.setdefault("analytics_version", 3)
    jobs_manifest.setdefault("jobs", {})
    jobs_manifest.setdefault("report_types", {})
    jobs_manifest.setdefault("job_status", {})
    collection_state.setdefault("downloaded_reports", {})

    try:
        discovered = paged(api_json, "reportTypes?pageSize=100", "reportTypes")
    except Exception as exc:
        warnings.append(f"Reporting API discovery unavailable: {error_label(exc)}")
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
        jobs_list_succeeded = True
    except Exception as exc:
        live_jobs = []
        jobs_list_succeeded = False
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
        if not jobs_list_succeeded:
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

    downloads = 0
    deferred = 0
    for report_type_id, job in sorted(by_type.items()):
        job_id = str(job.get("id") or "")
        if not job_id:
            continue
        try:
            reports = paged(api_json, f"jobs/{quote(job_id)}/reports?pageSize=100", "reports")
        except Exception as exc:
            warnings.append(f"Optional Reporting reports for {job_id} unavailable: {type(exc).__name__}")
            continue
        update_job_status(report_type_id, job, reports, jobs_manifest["job_status"], collected_at)
        for report in reports:
            report_id = str(report.get("id") or "")
            download_url = str(report.get("downloadUrl") or "")
            if not report_id or not download_url or report_id in collection_state["downloaded_reports"]:
                continue
            if downloads >= max(0, int(max_downloads)):
                deferred += 1
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
                downloads += 1
            except Exception as exc:
                warnings.append(f"Optional Reporting download {report_id} unavailable: {type(exc).__name__}")

    if deferred:
        warnings.append(
            f"Reporting download budget reached after {downloads} new reports; "
            f"{deferred} reports deferred to later runs"
        )

    stale = sorted(
        report_type_id
        for report_type_id, status in jobs_manifest["job_status"].items()
        if isinstance(status, dict) and status.get("status") == "stale"
    )
    if stale:
        shown = stale[:10]
        omitted = len(stale) - len(shown)
        suffix = f"; {omitted} omitted" if omitted else ""
        warnings.append(
            f"Reporting jobs stale with no available reports: {len(stale)} "
            f"({', '.join(shown)}{suffix})"
        )

    return ReportingSyncResult(files, warnings)
