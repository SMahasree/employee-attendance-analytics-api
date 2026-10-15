from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query

from ...core import database
from ...domain.attendance import compute_month_range, validate_date_parameter
from ...filters import attendance_filter
from ...analytics.pipelines import employee_monthly_pipeline, department_summary_pipeline, late_leaderboard_pipeline, department_trend_pipeline

router = APIRouter()


@router.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    from_date: Optional[str] = Query(None, alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_date: Optional[str] = Query(None, alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    if endpoint not in {"attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"}:
        raise HTTPException(status_code=404, detail="endpoint not found")
    if endpoint == "attendance_list":
        query = attendance_filter(emp_code, date_from, date_to, status)
        explain = database.db.command("explain", {
            "find": "attendance_logs",
            "filter": query,
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}
    if endpoint == "employee_monthly":
        if emp_code is None or month is None:
            raise HTTPException(status_code=422, detail="emp_code and month are required")
        start_date, end_date = compute_month_range(month)
        pipeline = employee_monthly_pipeline(emp_code, start_date, end_date)
        explain = database.db.command("explain", {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {},
        }, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}
    if endpoint == "department_summary":
        if month is None:
            raise HTTPException(status_code=422, detail="month is required")
        start_date, end_date = compute_month_range(month)
        pipeline = department_summary_pipeline(start_date, end_date, department)
        explain = database.db.command("explain", {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {},
        }, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}
    if endpoint == "late_leaderboard":
        if month is None:
            raise HTTPException(status_code=422, detail="month is required")
        start_date, end_date = compute_month_range(month)
        pipeline = late_leaderboard_pipeline(start_date, end_date, department, limit)
        explain = database.db.command("explain", {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {},
        }, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}
    if endpoint == "department_trend":
        if from_date is None or to_date is None:
            raise HTTPException(status_code=422, detail="from and to are required")
        start = validate_date_parameter(from_date, "from")
        end = validate_date_parameter(to_date, "to")
        if start > end:
            raise HTTPException(status_code=422, detail="from cannot be after to")
        day_diff = (end - start).days + 1
        if day_diff > 92:
            raise HTTPException(status_code=422, detail="date range cannot exceed 92 days")
        if department is None:
            raise HTTPException(status_code=422, detail="department is required")
        if database.db.employees.count_documents({"department": department}) == 0:
            raise HTTPException(status_code=404, detail="department not found")
        pipeline = department_trend_pipeline(department, from_date, day_diff)
        explain = database.db.command("explain", {
            "aggregate": "employees",
            "pipeline": pipeline,
            "cursor": {},
        }, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "employees", "explain": explain}
    raise HTTPException(status_code=404, detail="unknown endpoint")
