import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
