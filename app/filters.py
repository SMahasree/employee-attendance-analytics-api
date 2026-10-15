from typing import Any, Optional

from fastapi import HTTPException

from .domain.attendance import parse_date_str, validate_date_parameter


def attendance_filter(
    emp_code: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    status: Optional[str],
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if date_from or date_to:
        if date_from:
            validate_date_parameter(date_from, "date_from")
        if date_to:
            validate_date_parameter(date_to, "date_to")
        if date_from and date_to and parse_date_str(date_from) > parse_date_str(date_to):
            raise HTTPException(status_code=422, detail="date_from cannot be after date_to")
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
    if status is not None:
        query["status"] = status
    return query

