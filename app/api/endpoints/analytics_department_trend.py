from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ...core import database
from ...domain.attendance import validate_date_parameter
from ...analytics.pipelines import department_trend_pipeline

router = APIRouter()


@router.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_date: str = Query(..., alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict[str, Any]:
    start = validate_date_parameter(from_date, "from")
    end = validate_date_parameter(to_date, "to")
    if start > end:
        raise HTTPException(status_code=422, detail="from cannot be after to")
    day_diff = (end - start).days + 1
    if day_diff > 92:
        raise HTTPException(status_code=422, detail="date range cannot exceed 92 days")
    if database.db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(status_code=404, detail="department not found")
    pipeline = department_trend_pipeline(department, from_date, day_diff)
    return {"department": department, "items": list(database.db.employees.aggregate(pipeline))}
