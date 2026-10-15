from typing import Any, Optional

from fastapi import APIRouter, Query

from ...core import database
from ...domain.attendance import compute_month_range
from ...analytics.pipelines import late_leaderboard_pipeline

router = APIRouter()


@router.get("/analytics/leaderboard/late")
def late_leaderboard(month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), limit: int = Query(10, ge=1, le=50), department: Optional[str] = None) -> dict[str, Any]:
    start_date, end_date = compute_month_range(month)
    pipeline = late_leaderboard_pipeline(start_date, end_date, department, limit)
    return {"month": month, "items": list(database.db.attendance_logs.aggregate(pipeline))}
