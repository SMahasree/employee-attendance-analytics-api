from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from ...core import database
from ...domain.attendance import epoch_ms_to_datetime, normalize_record_values, serialize_record, truncate_datetime
from ...schemas import PunchOutRequest

router = APIRouter()


@router.post("/attendance/punch-out")
def punch_out(body: PunchOutRequest) -> dict[str, Any]:
    emp = database.db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="employee not found")
    punch_dt = epoch_ms_to_datetime(body.punched_at) or datetime.now(timezone.utc).replace(microsecond=0)
    record = database.db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None, "$lte": punch_dt + timedelta(seconds=1, milliseconds=-1)},
        },
        sort=[("punch_in", -1)],
    )
    if record is None:
        raise HTTPException(status_code=404, detail="no punch-in found")
    if record.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="record already punched out")
    punch_in = truncate_datetime(record["punch_in"])
    if punch_in is None:
        raise HTTPException(status_code=404, detail="no punch-in found")
    if punch_dt <= punch_in:
        raise HTTPException(status_code=422, detail="punch_out must be after punch_in")
    if (punch_dt - punch_in).total_seconds() > 86400:
        raise HTTPException(status_code=422, detail="punch_out must be within 24 hours of punch_in")
    final_values = normalize_record_values(emp, record["status"], punch_in, punch_dt)
    update = {
        "$set": {
            "punch_in": final_values["punch_in"],
            "punch_out": final_values["punch_out"],
            "work_hours": final_values["work_hours"],
            "overtime_minutes": final_values["overtime_minutes"],
            "half_day": final_values["half_day"],
        }
    }
    result = database.db.attendance_logs.update_one({"_id": record["_id"], "punch_out": None}, update)
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="record already punched out")
    doc = database.db.attendance_logs.find_one({"_id": record["_id"]})
    return serialize_record(doc)
