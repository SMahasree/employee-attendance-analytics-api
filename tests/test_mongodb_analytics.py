import unittest
from datetime import datetime, timezone

from tests import support

from app.api.endpoints import (
    admin_explain,
    analytics_department_summary,
    analytics_department_trend,
    analytics_employee_monthly,
    analytics_late_leaderboard,
)
from app.core import database


@unittest.skipUnless(
    support.TEST_MONGO_URI,
    "Set ATTENDANCE_TEST_MONGO_URI to run isolated real-MongoDB aggregation tests",
)
class MongoAnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.client.admin.command("ping")
        cls.database = database.db
        database.ensure_indexes(database.db)
        cls._load_fixture()

    @classmethod
    def tearDownClass(cls):
        database.client.drop_database(support.TEST_DATABASE)
        database.client.close()

    @classmethod
    def _load_fixture(cls):
        database.db.employees.insert_many([
            {
                "emp_code": "EMP92001",
                "name": "Analytics A",
                "email": "analytics.a@example.com",
                "department": "Analytics Test",
                "shift_start": "09:00",
                "shift_end": "18:00",
                "joined_on": "2026-10-01",
                "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
            },
            {
                "emp_code": "EMP92002",
                "name": "Analytics B",
                "email": "analytics.b@example.com",
                "department": "Analytics Test",
                "shift_start": "09:00",
                "shift_end": "18:00",
                "joined_on": "2026-10-01",
                "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
            },
            {
                "emp_code": "EMP92003",
                "name": "No Logs",
                "email": "analytics.empty@example.com",
                "department": "Analytics Test",
                "shift_start": "09:00",
                "shift_end": "18:00",
                "joined_on": "2026-10-01",
                "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
            },
            {
                "emp_code": "EMP92004",
                "name": "Future Joiner",
                "email": "analytics.future@example.com",
                "department": "Analytics Test",
                "shift_start": "09:00",
                "shift_end": "18:00",
                "joined_on": "2026-10-14",
                "created_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
            },
        ])
        shared_log = {
            "date": "2026-10-12",
            "status": "PRESENT",
            "punch_in": datetime(2026, 10, 12, 3, 15, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 10, 12, 12, 30, tzinfo=timezone.utc),
            "work_hours": 9.25,
            "late_minutes": 15,
            "overtime_minutes": 30,
            "half_day": False,
            "history": [],
        }
        database.db.attendance_logs.insert_many([
            {"emp_code": code, **shared_log}
            for code in ("EMP92001", "EMP92002")
        ] + [{
            "emp_code": "EMP92001",
            "date": "2026-10-14",
            "status": "LEAVE",
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        }])

    def test_employee_monthly_includes_stored_totals_and_empty_results(self):
        result = analytics_employee_monthly.employee_monthly("EMP92001", month="2026-10")

        self.assertEqual(result["working_days"], 22)
        self.assertEqual(result["present_days"], 1)
        self.assertEqual(result["leave_days"], 1)
        self.assertEqual(result["late_count"], 1)
        self.assertEqual(result["total_late_minutes"], 15)
        self.assertEqual(result["total_overtime_minutes"], 30)

        empty = analytics_employee_monthly.employee_monthly("EMP92003", month="2026-10")
        self.assertEqual(empty["present_days"], 0)
        self.assertEqual(empty["leave_days"], 0)
        self.assertEqual(empty["total_late_minutes"], 0)

    def test_department_summary_counts_zero_log_staff_and_filters_future_joiners(self):
        result = analytics_department_summary.department_summary(month="2026-10", department="Analytics Test")
        item = result["items"][0]

        self.assertEqual(item["headcount"], 4)
        self.assertEqual(item["present_days"], 2)
        self.assertEqual(item["avg_work_hours"], 9.25)
        self.assertEqual(item["late_count"], 2)
        self.assertEqual(item["total_late_minutes"], 30)
        self.assertEqual(item["leave_count"], 1)

    def test_late_leaderboard_ties_share_rank_even_at_limit(self):
        result = analytics_late_leaderboard.late_leaderboard(month="2026-10", limit=1, department="Analytics Test")

        self.assertEqual([(row["emp_code"], row["rank"]) for row in result["items"]], [
            ("EMP92001", 1),
            ("EMP92002", 1),
        ])

    def test_trend_fills_days_and_uses_seven_calendar_rows(self):
        result = analytics_department_trend.department_trend(
            "Analytics Test",
            from_date="2026-10-07",
            to_date="2026-10-13",
        )
        rows = result["items"]

        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[3]["date"], "2026-10-10")
        self.assertFalse(rows[3]["is_working_day"])
        self.assertIsNone(rows[3]["attendance_rate"])
        self.assertEqual(rows[5]["headcount"], 3)
        self.assertEqual(rows[5]["present_count"], 2)
        self.assertEqual(rows[5]["attendance_rate"], 66.6667)
        self.assertEqual(rows[6]["headcount"], 3)
        self.assertEqual(rows[6]["present_count"], 0)
        self.assertEqual(rows[6]["attendance_rate"], 0.0)
        self.assertEqual(rows[6]["moving_avg_7d"], 13.3333)

    def test_trend_excludes_attendance_before_employee_join_date(self):
        database.db.attendance_logs.insert_one({
            "emp_code": "EMP92004",
            "date": "2026-10-12",
            "status": "PRESENT",
            "punch_in": datetime(2026, 10, 12, 3, 15, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 10, 12, 12, 30, tzinfo=timezone.utc),
            "work_hours": 9.25,
            "late_minutes": 15,
            "overtime_minutes": 30,
            "half_day": False,
            "history": [],
        })

        result = analytics_department_trend.department_trend(
            "Analytics Test",
            from_date="2026-10-12",
            to_date="2026-10-12",
        )

        self.assertEqual(result["items"][0]["headcount"], 3)
        self.assertEqual(result["items"][0]["present_count"], 2)
        self.assertEqual(result["items"][0]["attendance_rate"], 66.6667)

    def test_expected_indexes_and_explain_routes(self):
        employee_indexes = database.db.employees.index_information()
        attendance_indexes = database.db.attendance_logs.index_information()

        self.assertTrue(any(index.get("unique") and index["key"] == [("emp_code", 1)] for index in employee_indexes.values()))
        self.assertTrue(any(index.get("unique") and index["key"] == [("emp_code", 1), ("date", 1)] for index in attendance_indexes.values()))
        self.assertIn("date_1_emp_code_1", attendance_indexes)
        explain_args = {
            "month": "2026-10",
            "department": "Analytics Test",
            "limit": 10,
            "date_from": None,
            "date_to": None,
            "status": None,
            "from_date": "2026-10-07",
            "to_date": "2026-10-13",
            "page": 1,
            "page_size": 20,
        }
        explanations = [
            admin_explain.explain_endpoint("attendance_list", emp_code="EMP92001", **{**explain_args, "month": None, "from_date": None, "to_date": None}),
            admin_explain.explain_endpoint("employee_monthly", emp_code="EMP92001", **explain_args),
            admin_explain.explain_endpoint("department_summary", emp_code=None, **explain_args),
            admin_explain.explain_endpoint("late_leaderboard", emp_code=None, **explain_args),
            admin_explain.explain_endpoint("department_trend", emp_code=None, **explain_args),
        ]
        self.assertEqual(len(explanations), 5)
        self.assertTrue(all("explain" in result for result in explanations))


if __name__ == "__main__":
    unittest.main()
