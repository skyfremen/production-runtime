import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

import analytics_reporting as reporting
from analytics_reporting import sync_reporting


class FakeReportingApi:
    def __init__(self, fail_reports_for=None):
        self.calls = []
        self.created = []
        self.fail_reports_for = fail_reports_for

    def json(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.startswith("reportTypes"):
            return {
                "reportTypes": [
                    {"id": "channel_basic_a3", "name": "Basic", "systemManaged": False},
                    {"id": "channel_device_os_a3", "name": "Device", "systemManaged": False},
                    {"id": "content_owner_basic_a3", "name": "Owner", "systemManaged": False},
                    {"id": "channel_old_a1", "name": "Old", "deprecateTime": "2020-01-01T00:00:00Z"},
                ]
            }
        if path == "jobs?pageSize=100":
            return {"jobs": [{"id": "job-basic", "reportTypeId": "channel_basic_a3"}]}
        if method == "POST" and path == "jobs":
            self.created.append(body["reportTypeId"])
            return {"id": "job-device", "reportTypeId": body["reportTypeId"], "createTime": "2026-09-26T00:00:00Z"}
        if path == "jobs/job-device/reports?pageSize=100":
            if self.fail_reports_for == "job-device":
                raise RuntimeError("optional unavailable")
            return {"reports": []}
        if path == "jobs/job-basic/reports?pageSize=100":
            return {
                "reports": [
                    {
                        "id": "report-1",
                        "jobId": "job-basic",
                        "startTime": "2026-09-24T00:00:00Z",
                        "endTime": "2026-09-25T00:00:00Z",
                        "createTime": "2026-09-26T00:00:00Z",
                        "downloadUrl": "https://example.invalid/report-1",
                    }
                ]
            }
        raise AssertionError((method, path, body))

    def bytes(self, url):
        self.calls.append(("DOWNLOAD", url, None))
        return b"date,video_id,views\n2026-09-24,abc,10\n"


class ReportingSyncTests(unittest.TestCase):
    def test_job_status_waiting_active_and_stale_boundaries(self):
        now = datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)
        statuses = {}
        waiting = reporting.update_job_status(
            "channel_waiting",
            {"id": "job-waiting", "createTime": "2026-09-26T00:00:00Z"},
            [],
            statuses,
            now,
        )
        self.assertEqual("waiting_for_first_report", waiting["status"])
        self.assertEqual(0, waiting["available_report_count"])
        self.assertNotIn("first_report_observed_at", waiting)

        active = reporting.update_job_status(
            "channel_waiting",
            {"id": "job-waiting", "createTime": "2026-09-26T00:00:00Z"},
            [{"id": "report-1", "endTime": "2026-09-26T23:00:00Z"}],
            statuses,
            now + timedelta(hours=1),
        )
        self.assertEqual("active", active["status"])
        self.assertEqual(1, active["available_report_count"])
        self.assertEqual("2026-09-26T23:00:00Z", active["latest_report_end_time"])
        self.assertEqual("2026-09-27T01:00:00Z", active["first_report_observed_at"])

        first = now - timedelta(hours=47, minutes=59, seconds=59)
        statuses["channel_boundary"] = {"first_observed_at": reporting.iso_z(first)}
        before = reporting.update_job_status(
            "channel_boundary", {"id": "job-boundary"}, [], statuses, now
        )
        self.assertEqual("waiting_for_first_report", before["status"])
        exact = reporting.update_job_status(
            "channel_boundary",
            {"id": "job-boundary"},
            [],
            statuses,
            now + timedelta(seconds=1),
        )
        self.assertEqual("stale", exact["status"])

    def test_multiple_stale_jobs_emit_one_bounded_warning(self):
        report_types = [f"channel_type_{index:02d}" for index in range(12)]

        def api(method, path, body=None):
            if path.startswith("reportTypes"):
                return {"reportTypes": [{"id": item, "systemManaged": False} for item in report_types]}
            if path == "jobs?pageSize=100":
                return {
                    "jobs": [
                        {
                            "id": f"job-{index:02d}",
                            "reportTypeId": item,
                            "createTime": "2026-09-20T00:00:00Z",
                        }
                        for index, item in enumerate(report_types)
                    ]
                }
            if path.endswith("/reports?pageSize=100"):
                return {"reports": []}
            raise AssertionError((method, path, body))

        with tempfile.TemporaryDirectory() as tmp:
            result = sync_reporting(
                "token",
                Path(tmp),
                {"jobs": {}},
                {"downloaded_reports": {}},
                api,
                lambda _url: b"",
                datetime(2026, 9, 27, tzinfo=timezone.utc),
            )

        stale_warnings = [warning for warning in result.warnings if "stale" in warning.casefold()]
        self.assertEqual(1, len(stale_warnings))
        self.assertIn("12", stale_warnings[0])
        self.assertIn("2 omitted", stale_warnings[0])
        self.assertIn(report_types[9], stale_warnings[0])
        self.assertNotIn(report_types[10], stale_warnings[0])

    def test_report_listing_failure_preserves_previous_status(self):
        previous = {
            "status": "active",
            "first_observed_at": "2026-09-20T00:00:00Z",
            "last_checked_at": "2026-09-26T00:00:00Z",
            "available_report_count": 3,
            "latest_report_end_time": "2026-09-25T00:00:00Z",
            "first_report_observed_at": "2026-09-21T00:00:00Z",
        }
        jobs = {"jobs": {}, "job_status": {"channel_device_os_a3": dict(previous)}}
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeReportingApi(fail_reports_for="job-device")
            sync_reporting(
                "token",
                Path(tmp),
                jobs,
                {"downloaded_reports": {}},
                api.json,
                api.bytes,
                datetime(2026, 9, 27, tzinfo=timezone.utc),
            )
        self.assertEqual(previous, jobs["job_status"]["channel_device_os_a3"])

    def test_discovery_http_error_reports_safe_status_and_reason(self):
        def forbidden(_method, _path, _body=None):
            raise HTTPError("https://example.invalid", 403, "Forbidden", {}, None)

        with tempfile.TemporaryDirectory() as tmp:
            result = sync_reporting(
                "token", Path(tmp), {"jobs": {}}, {"downloaded_reports": {}},
                forbidden, lambda _url: b"", datetime(2026, 9, 26, tzinfo=timezone.utc),
            )

        self.assertEqual(result.warnings, ["Reporting API discovery unavailable: HTTP 403 Forbidden"])

    def test_jobs_list_failure_does_not_create_possibly_duplicate_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeReportingApi()
            original = api.json

            def fail_jobs(method, path, body=None):
                if path == "jobs?pageSize=100":
                    raise RuntimeError("temporary list failure")
                return original(method, path, body)

            result = sync_reporting(
                "token", Path(tmp), {"jobs": {}}, {"downloaded_reports": {}},
                fail_jobs, api.bytes, datetime(2026, 9, 26, tzinfo=timezone.utc),
            )

            self.assertEqual(api.created, [])
            self.assertTrue(any("jobs list unavailable" in warning for warning in result.warnings))

    def test_discovers_relevant_types_reuses_jobs_and_creates_only_missing_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeReportingApi()
            jobs = {"jobs": {}}
            state = {"downloaded_reports": {}}
            result = sync_reporting(
                "token", Path(tmp), jobs, state, api.json, api.bytes,
                datetime(2026, 9, 26, tzinfo=timezone.utc),
            )

            self.assertEqual(api.created, ["channel_device_os_a3"])
            self.assertEqual(set(jobs["jobs"]), {"channel_basic_a3", "channel_device_os_a3"})
            self.assertNotIn("content_owner_basic_a3", jobs["report_types"])
            self.assertNotIn("channel_old_a1", jobs["report_types"])
            self.assertEqual(len(result.files), 2)

    def test_downloaded_report_id_is_not_downloaded_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeReportingApi()
            jobs = {"jobs": {"channel_basic_a3": {"id": "job-basic", "reportTypeId": "channel_basic_a3"}}}
            state = {"downloaded_reports": {"report-1": {"path": "raw/basic/channel_basic_a3/report-1.csv"}}}
            result = sync_reporting(
                "token", Path(tmp), jobs, state, api.json, api.bytes,
                datetime(2026, 9, 26, tzinfo=timezone.utc),
            )

            self.assertFalse(any(call[0] == "DOWNLOAD" for call in api.calls))
            self.assertEqual(result.files, [])

    def test_one_optional_job_failure_records_warning_and_keeps_other_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeReportingApi(fail_reports_for="job-device")
            result = sync_reporting(
                "token", Path(tmp), {"jobs": {}}, {"downloaded_reports": {}},
                api.json, api.bytes, datetime(2026, 9, 26, tzinfo=timezone.utc),
            )

            self.assertTrue(any("job-device" in warning for warning in result.warnings))
            self.assertTrue(any(path.name == "report-1.csv" for path in result.files))
            metadata = json.loads(next(path for path in result.files if path.suffix == ".json").read_text())
            self.assertEqual(metadata["report_type_id"], "channel_basic_a3")

    def test_download_budget_defers_remaining_reports_for_later_runs(self):
        api = FakeReportingApi()
        original = api.json

        def three_reports(method, path, body=None):
            response = original(method, path, body)
            if path == "jobs/job-basic/reports?pageSize=100":
                response["reports"] = [
                    {"id": f"report-{i}", "downloadUrl": f"https://example.invalid/report-{i}"}
                    for i in range(1, 4)
                ]
            return response

        with tempfile.TemporaryDirectory() as tmp:
            state = {"downloaded_reports": {}}
            result = sync_reporting(
                "token", Path(tmp), {"jobs": {}}, state, three_reports, api.bytes,
                datetime(2026, 9, 26, tzinfo=timezone.utc), max_downloads=2,
            )

        self.assertEqual(len(state["downloaded_reports"]), 2)
        self.assertEqual(sum(call[0] == "DOWNLOAD" for call in api.calls), 2)
        self.assertTrue(any("download budget" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
