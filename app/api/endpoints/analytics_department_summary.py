from typing import Any, Optional

from fastapi import APIRouter, Query

from ...core import database
from ...domain.attendance import compute_month_range, round_half_up
from ...analytics.pipelines import department_summary_pipeline

router = APIRouter()


@router.get("/analytics/departments/summary")
def department_summary(month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), department: Optional[str] = None) -> dict[str, Any]:
    start_date, end_date = compute_month_range(month)
    pipeline = department_summary_pipeline(start_date, end_date, department)
    rows = list(database.db.attendance_logs.aggregate(pipeline))
    if department is None:
        department_names = sorted(database.db.employees.distinct("department", {"joined_on": {"$lte": end_date}}))
    else:
        department_names = [department] if database.db.employees.count_documents({"department": department, "joined_on": {"$lte": end_date}}) else []
    results = []
    for dept in department_names:
        headcount = database.db.employees.count_documents({"department": dept, "joined_on": {"$lte": end_date}})
        if headcount == 0:
            continue
        row = next((r for r in rows if r["_id"] == dept), {})
        avg_work_hours = row.get("avg_work_hours")
        results.append({
            "department": dept,
            "headcount": headcount,
            "present_days": row.get("present_days", 0),
            "avg_work_hours": round_half_up(float(avg_work_hours), 2) if avg_work_hours is not None else None,
            "late_count": row.get("late_count", 0),
            "total_late_minutes": row.get("total_late_minutes", 0),
            "leave_count": row.get("leave_count", 0),
            "on_duty_count": row.get("on_duty_count", 0),
        })
    return {"month": month, "items": results}
