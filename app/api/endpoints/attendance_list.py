from typing import Any, Literal, Optional

from fastapi import APIRouter, Query

from ...core import database
from ...domain.attendance import serialize_record
from ...filters import attendance_filter

router = APIRouter()


@router.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    query = attendance_filter(emp_code, date_from, date_to, status)
    total = database.db.attendance_logs.count_documents(query)
    docs = list(database.db.attendance_logs.find(query).sort([("date", -1), ("emp_code", 1)]).skip((page - 1) * page_size).limit(page_size))
    return {"items": [serialize_record(item) for item in docs], "total": total, "page": page, "page_size": page_size}
