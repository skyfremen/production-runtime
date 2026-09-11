import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from output.execute import (
    SCHEDULE_FRESHNESS_BUFFER_MINUTES,
    pre_generation_authorization,
    scheduled_slot_guard,
)
from output.state import RecoveryBlocked
from test_schema import valid_request


class ScheduledSlotGuardTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 10, 0, 0, 0, tzinfo=timezone.utc)

    def request_at(self, when):
        return {
            "publication": {
                "mode": "scheduled",
                "timezone": "Asia/Singapore",
                "publish_at": when.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        }

    def full_request_at(self, when):
        request = valid_request()
        request["publication"] = self.request_at(when)["publication"]
        return request

    def immediate_request(self):
        request = valid_request()
        request["publication"] = {
            "mode": "immediate",
            "timezone": "Asia/Singapore",
            "publish_at": None,
        }
        return request

    def test_policy_is_exactly_ten_minutes(self):
        self.assertEqual(SCHEDULE_FRESHNESS_BUFFER_MINUTES, 10)

    def test_past_slot_is_skipped(self):
        result = scheduled_slot_guard(
            self.request_at(self.now - timedelta(minutes=1)), now_utc=self.now
        )
        self.assertTrue(result["skip"])
        self.assertLess(result["remaining_seconds"], 0)

    def test_exactly_ten_minutes_is_skipped(self):
        result = scheduled_slot_guard(
            self.request_at(self.now + timedelta(minutes=10)), now_utc=self.now
        )
        self.assertTrue(result["skip"])
        self.assertEqual(result["remaining_seconds"], 600)

    def test_less_than_ten_minutes_is_skipped(self):
        result = scheduled_slot_guard(
            self.request_at(self.now + timedelta(minutes=9, seconds=59)),
            now_utc=self.now,
        )
        self.assertTrue(result["skip"])

    def test_more_than_ten_minutes_can_generate(self):
        result = scheduled_slot_guard(
            self.request_at(self.now + timedelta(minutes=10, seconds=1)),
            now_utc=self.now,
        )
        self.assertFalse(result["skip"])
        self.assertIsNone(result["reason"])

    def test_immediate_publication_has_no_schedule_freshness_block(self):
        result = scheduled_slot_guard(self.immediate_request(), now_utc=self.now)
        self.assertFalse(result["skip"])
        self.assertIsNone(result["reason"])
        self.assertIsNone(result["remaining_seconds"])

    def test_immediate_publication_rejects_publish_at(self):
        request = self.immediate_request()
        request["publication"]["publish_at"] = "2026-09-10T01:00:00Z"
        with self.assertRaisesRegex(RecoveryBlocked, "publish_at=null"):
            scheduled_slot_guard(request, now_utc=self.now)

    def test_missing_publication_is_rejected(self):
        with self.assertRaisesRegex(RecoveryBlocked, "Publication contract"):
            scheduled_slot_guard({}, now_utc=self.now)

    def test_skipped_slot_avoids_upload_contract_and_inventory_scan(self):
        request = self.full_request_at(self.now + timedelta(minutes=5))
        with (
            patch("output.execute.build_upload_body") as body,
            patch("output.execute.authorize_fresh_upload") as authorize,
        ):
            result = pre_generation_authorization(
                request, {}, Mock(), Mock(), Mock(), now_utc=self.now
            )
        self.assertTrue(result["skip"])
        body.assert_not_called()
        authorize.assert_not_called()

    def test_valid_fresh_slot_checks_contract_before_inventory_authorization(self):
        request = self.full_request_at(self.now + timedelta(minutes=11))
        events = []
        with (
            patch(
                "output.execute.build_upload_body",
                side_effect=lambda *args, **kwargs: events.append("body") or {},
            ),
            patch(
                "output.execute.authorize_fresh_upload",
                side_effect=lambda *args, **kwargs: events.append("authorize"),
            ),
        ):
            result = pre_generation_authorization(
                request, {}, Mock(), Mock(), Mock(), now_utc=self.now
            )
        self.assertFalse(result["skip"])
        self.assertEqual(events, ["body", "authorize"])

    def test_immediate_publication_checks_contract_before_inventory_authorization(self):
        request = self.immediate_request()
        events = []
        with (
            patch(
                "output.execute.build_upload_body",
                side_effect=lambda *args, **kwargs: events.append("body") or {},
            ),
            patch(
                "output.execute.authorize_fresh_upload",
                side_effect=lambda *args, **kwargs: events.append("authorize"),
            ),
        ):
            result = pre_generation_authorization(
                request, {}, Mock(), Mock(), Mock(), now_utc=self.now
            )
        self.assertFalse(result["skip"])
        self.assertEqual(events, ["body", "authorize"])


if __name__ == "__main__":
    unittest.main()
