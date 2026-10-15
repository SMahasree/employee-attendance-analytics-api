from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pymongo.errors import DuplicateKeyError

from ...core import database
from ...domain.attendance import compute_late_minutes, epoch_ms_to_datetime, get_shift_context_for_datetime, serialize_record
from ...schemas import PunchInRequest

router = APIRouter()


@router.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInRequest) -> dict[str, Any]:
    emp = database.db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="employee not found")
    punch_dt = epoch_ms_to_datetime(body.punched_at) or datetime.now(timezone.utc).replace(microsecond=0)
    record_date = get_shift_context_for_datetime(emp, punch_dt)["record_date"]
    existing = database.db.attendance_logs.find_one({"emp_code": body.emp_code, "date": record_date})
    if existing is not None:
        raise HTTPException(status_code=409, detail="already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": record_date,
        "status": body.status,
        "punch_in": punch_dt,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(emp, punch_dt),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        database.db.attendance_logs.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="already punched in for this date") from exc
    return serialize_record(database.db.attendance_logs.find_one({"emp_code": body.emp_code, "date": record_date}))
