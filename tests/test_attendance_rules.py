import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import ValidationError

from tests import support
from app import main as api

try:
    import mongomock
except ImportError:  # pragma: no cover
    mongomock = None


def local_instant(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class TimestampAndShiftTests(unittest.TestCase):
    employee = {"emp_code": "EMP99904", "shift_start": "22:00", "shift_end": "06:00"}

    def test_reported_timestamp_decodes_to_the_instant_that_was_sent(self):
        submitted = api.epoch_ms_to_datetime(1791929700000)

        self.assertEqual(submitted.isoformat(), "2026-10-13T22:15:00+00:00")
        self.assertEqual(submitted.astimezone(api.IST).isoformat(), "2026-10-14T03:45:00+05:30")
        self.assertEqual(api.compute_late_minutes(self.employee, submitted), 345)

    def test_correct_2215_ist_epoch_has_fifteen_minutes_lateness(self):
        submitted = api.epoch_ms_to_datetime(1791909900000)

        self.assertEqual(submitted.astimezone(api.IST).isoformat(), "2026-10-13T22:15:00+05:30")
        self.assertEqual(api.get_shift_context_for_datetime(self.employee, submitted)["record_date"], "2026-10-13")
        self.assertEqual(api.compute_late_minutes(self.employee, submitted), 15)

    def test_overnight_lateness_boundaries_and_preceding_shift_date(self):
        cases = [
            ("2026-10-13T22:00:00+05:30", 0, "2026-10-13"),
            ("2026-10-13T22:10:00+05:30", 0, "2026-10-13"),
            ("2026-10-13T22:11:00+05:30", 11, "2026-10-13"),
            ("2026-10-13T22:15:00+05:30", 15, "2026-10-13"),
            ("2026-10-14T00:15:00+05:30", 135, "2026-10-13"),
        ]

        for timestamp, expected_late, expected_date in cases:
            with self.subTest(timestamp=timestamp):
                instant = local_instant(timestamp)
                context = api.get_shift_context_for_datetime(self.employee, instant)
                self.assertEqual(context["record_date"], expected_date)
                self.assertEqual(api.compute_late_minutes(self.employee, instant), expected_late)

    def test_day_shift_grace_period_boundary(self):
        employee = {"emp_code": "EMP90001", "shift_start": "09:30", "shift_end": "18:30"}

        self.assertEqual(api.compute_late_minutes(employee, local_instant("2026-10-12T09:40:00+05:30")), 0)
        self.assertEqual(api.compute_late_minutes(employee, local_instant("2026-10-12T09:40:01+05:30")), 10)

    def test_overnight_work_hours_and_overtime_threshold(self):
        punch_in = local_instant("2026-10-13T22:00:00+05:30")
        shift_end = local_instant("2026-10-14T06:00:00+05:30")

        self.assertEqual(api.compute_work_hours(punch_in, shift_end + timedelta(minutes=30)), 8.5)
        self.assertEqual(api.compute_overtime_minutes(self.employee, shift_end + timedelta(minutes=29, seconds=59), punch_in), 0)
        self.assertEqual(api.compute_overtime_minutes(self.employee, shift_end + timedelta(minutes=30), punch_in), 30)
        self.assertEqual(api.compute_overtime_minutes(self.employee, shift_end + timedelta(minutes=30, seconds=59), punch_in), 30)

    def test_half_day_uses_rounded_work_hours(self):
        start = local_instant("2026-10-12T09:00:00+05:30")

        below_threshold = api.compute_work_hours(start, start + timedelta(seconds=16181))
        at_rounded_threshold = api.compute_work_hours(start, start + timedelta(seconds=16182))

        self.assertEqual(below_threshold, 4.49)
        self.assertTrue(api.compute_half_day(below_threshold))
        self.assertEqual(at_rounded_threshold, 4.5)
        self.assertFalse(api.compute_half_day(at_rounded_threshold))

    def test_epoch_milliseconds_are_strict_and_whole_seconds_are_returned(self):
        valid = api.PunchInRequest(emp_code="EMP90001", punched_at=1791909900123)
        converted = api.epoch_ms_to_datetime(valid.punched_at)

        self.assertEqual(converted.microsecond, 0)
        self.assertEqual(api.datetime_to_epoch_ms(converted), 1791909900000)
        for invalid in (1791909900, 1791909900123.0, "1791909900123", 4102444800001):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    api.PunchInRequest.model_validate({"emp_code": "EMP90001", "punched_at": invalid})

    def test_invalid_calendar_dates_are_rejected(self):
        for invalid in ("not-a-date", "2026-02-30", "2026-2-03"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    api.parse_date_str(invalid)


@unittest.skipIf(mongomock is None, "mongomock is required for isolated API tests")
class AttendanceApiTests(unittest.TestCase):
    def setUp(self):
        self.client = mongomock.MongoClient(tz_aware=True)
        self.original_db = api.db
        api.db = self.client["attendance_unit_tests"]
        api.ensure_indexes()
        self.employee("EMP90001")

    def tearDown(self):
        api.db = self.original_db
        self.client.close()

    def employee(self, emp_code: str, shift_start: str = "09:00", shift_end: str = "18:00"):
        return api.create_employee(api.EmployeeCreate(
            emp_code=emp_code,
            name="Test Employee",
            email=f"{emp_code.lower()}@example.com",
            department="Testing",
            shift_start=shift_start,
            shift_end=shift_end,
            joined_on="2026-10-01",
        ))

    def test_duplicate_punch_in_is_conflict(self):
        request = api.PunchInRequest(emp_code="EMP90001", punched_at=1791776700000)
        self.assertEqual(api.punch_in(request)["emp_code"], "EMP90001")

        with self.assertRaises(HTTPException) as raised:
            api.punch_in(request)
        self.assertEqual(raised.exception.status_code, 409)

    def test_duplicate_employee_code_is_conflict(self):
        with self.assertRaises(HTTPException) as raised:
            self.employee("EMP90001")
        self.assertEqual(raised.exception.status_code, 409)

    def test_unknown_employee_and_punch_out_without_punch_in(self):
        with self.assertRaises(HTTPException) as unknown:
            api.punch_in(api.PunchInRequest(emp_code="EMP99999", punched_at=1791776700000))
        self.assertEqual(unknown.exception.status_code, 404)

        with self.assertRaises(HTTPException) as no_record:
            api.punch_out(api.PunchOutRequest(emp_code="EMP90001", punched_at=1791810000000))
        self.assertEqual(no_record.exception.status_code, 404)

    def test_invalid_order_and_more_than_24_hours_are_rejected(self):
        punch_in = api.PunchInRequest(emp_code="EMP90001", punched_at=1791776700000)
        api.punch_in(punch_in)

        for punch_out in (1791776700000, 1791863101000):
            with self.subTest(punch_out=punch_out):
                with self.assertRaises(HTTPException) as raised:
                    api.punch_out(api.PunchOutRequest(emp_code="EMP90001", punched_at=punch_out))
                self.assertEqual(raised.exception.status_code, 422)

    def test_punch_out_exactly_24_hours_after_punch_in_is_allowed(self):
        self.employee("EMP90002")
        api.punch_in(api.PunchInRequest(emp_code="EMP90002", punched_at=1791776700000))
        punched_out = api.punch_out(api.PunchOutRequest(emp_code="EMP90002", punched_at=1791863100000))

        self.assertEqual(punched_out["work_hours"], 24.0)

    def test_duplicate_punch_out_does_not_overwrite_record(self):
        api.punch_in(api.PunchInRequest(emp_code="EMP90001", punched_at=1791776700000))
        first = api.punch_out(api.PunchOutRequest(emp_code="EMP90001", punched_at=1791810000000))
        self.assertEqual(first["work_hours"], 9.25)

        with self.assertRaises(HTTPException) as raised:
            api.punch_out(api.PunchOutRequest(emp_code="EMP90001", punched_at=1791813600000))
        self.assertEqual(raised.exception.status_code, 409)
        listed = api.list_attendance(
            emp_code="EMP90001",
            date_from=None,
            date_to=None,
            status=None,
            page=1,
            page_size=20,
        )
        self.assertEqual(listed["items"][0]["punch_out"], 1791810000000)

    def test_reported_night_shift_regression_with_correct_epoch(self):
        self.employee("EMP90002", "22:00", "06:00")
        result = api.punch_in(api.PunchInRequest(
            emp_code="EMP90002",
            punched_at=1791909900000,
        ))

        self.assertEqual(result["date"], "2026-10-13")
        self.assertEqual(result["punch_in"], 1791909900000)
        self.assertEqual(result["late_minutes"], 15)

    def test_employee_pagination_and_department_filter_totals(self):
        self.employee("EMP90002")
        self.employee("EMP90003")
        page = api.list_employees(department="Testing", page=2, page_size=1)

        self.assertEqual(page["total"], 3)
        self.assertEqual(page["page"], 2)
        self.assertEqual(page["page_size"], 1)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["emp_code"], "EMP90002")

    def test_overnight_punch_in_and_punch_out_use_shift_start_date(self):
        self.employee("EMP90002", "22:00", "06:00")
        start = api.epoch_ms_to_datetime(1791909000000)
        end = api.epoch_ms_to_datetime(1791939600000)
        punched_in = api.punch_in(api.PunchInRequest(emp_code="EMP90002", punched_at=1791909000000))
        punched_out = api.punch_out(api.PunchOutRequest(emp_code="EMP90002", punched_at=1791939600000))

        self.assertEqual(punched_in["date"], "2026-10-13")
        self.assertEqual(punched_in["late_minutes"], 0)
        self.assertEqual(punched_out["date"], "2026-10-13")
        self.assertEqual(punched_out["work_hours"], api.compute_work_hours(start, end))
        self.assertEqual(punched_out["overtime_minutes"], 30)

    def test_regularization_recomputes_and_appends_audit_and_rejects_noop(self):
        api.punch_in(api.PunchInRequest(emp_code="EMP90001", punched_at=1791776700000))
        api.punch_out(api.PunchOutRequest(emp_code="EMP90001", punched_at=1791810000000))
        corrected = api.regularize_attendance(
            "EMP90001",
            "2026-10-12",
            api.RegularizeRequest(status="WFH", reason="Correct status", regularized_by="test-suite"),
        )

        self.assertEqual(corrected["history"][0]["changes"]["status"], {"from": "PRESENT", "to": "WFH"})
        self.assertEqual(corrected["history"][0]["reason"], "Correct status")
        self.assertIsInstance(corrected["history"][0]["at"], int)
        self.assertEqual(corrected["work_hours"], 9.25)
        with self.assertRaises(HTTPException) as no_op:
            api.regularize_attendance(
                "EMP90001",
                "2026-10-12",
                api.RegularizeRequest(status="WFH", reason="No change requested", regularized_by="test-suite"),
            )
        self.assertEqual(no_op.exception.status_code, 422)

    def test_invalid_attendance_date_filter_returns_422(self):
        with self.assertRaises(HTTPException) as raised:
            api.list_attendance(emp_code=None, date_from="2026-02-30", page=1, page_size=20)
        self.assertEqual(raised.exception.status_code, 422)

    def test_attendance_date_range_is_inclusive_and_reversed_range_fails(self):
        api.punch_in(api.PunchInRequest(emp_code="EMP90001", punched_at=1791776700000))
        matches = api.list_attendance(
            emp_code="EMP90001",
            date_from="2026-10-12",
            date_to="2026-10-12",
            status="PRESENT",
            page=1,
            page_size=20,
        )
        self.assertEqual(matches["total"], 1)

        with self.assertRaises(HTTPException) as raised:
            api.list_attendance(
                emp_code="EMP90001",
                date_from="2026-10-13",
                date_to="2026-10-12",
                status=None,
                page=1,
                page_size=20,
            )
        self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
