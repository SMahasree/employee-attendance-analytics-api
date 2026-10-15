from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ...core import database
from ...domain.attendance import compute_month_range, month_working_days, round_percentage_half_up
from ...analytics.pipelines import employee_monthly_pipeline

router = APIRouter()


@router.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")) -> dict[str, Any]:
    employee = database.db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    start_date, end_date = compute_month_range(month)
    result = list(database.db.attendance_logs.aggregate(employee_monthly_pipeline(emp_code, start_date, end_date)))
    row = result[0] if result else {"present_days": 0, "leave_days": 0, "late_count": 0, "total_late_minutes": 0, "total_overtime_minutes": 0}
    working_days = month_working_days(employee["joined_on"], month)
    attendance_pct = None if working_days == 0 else round_percentage_half_up(row["present_days"], working_days)
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": row["present_days"],
        "leave_days": row["leave_days"],
        "late_count": row["late_count"],
        "total_late_minutes": row["total_late_minutes"],
        "total_overtime_minutes": row["total_overtime_minutes"],
        "attendance_pct": attendance_pct,
    }
