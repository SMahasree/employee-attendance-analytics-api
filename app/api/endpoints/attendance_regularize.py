from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from ...core import database
from ...domain.attendance import epoch_ms_to_datetime, get_shift_context_for_datetime, normalize_record_values, serialize_record, truncate_datetime, validate_date_parameter
from ...schemas import RegularizeRequest

router = APIRouter()


@router.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, payload: RegularizeRequest) -> dict[str, Any]:
    validate_date_parameter(date, "date")
    employee = database.db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    record = database.db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(status_code=404, detail="attendance record not found")

    new_status = payload.status if payload.status is not None else record["status"]
    new_punch_in = truncate_datetime(record.get("punch_in"))
    new_punch_out = truncate_datetime(record.get("punch_out"))
    if payload.punch_in is not None:
        new_punch_in = epoch_ms_to_datetime(payload.punch_in)
    if payload.punch_out is not None:
        new_punch_out = epoch_ms_to_datetime(payload.punch_out)

    if new_status in {"ABSENT", "LEAVE"}:
        if payload.punch_in is not None or payload.punch_out is not None:
            raise HTTPException(status_code=422, detail="ABSENT and LEAVE records must not include punch times")
        new_punch_in = None
        new_punch_out = None
    else:
        if new_punch_in is None:
            raise HTTPException(status_code=422, detail="presence status requires a punch_in")
        if get_shift_context_for_datetime(employee, new_punch_in)["record_date"] != date:
            raise HTTPException(status_code=422, detail="punch_in must remain on the record date")
        if new_punch_out is not None and new_punch_out <= new_punch_in:
            raise HTTPException(status_code=422, detail="punch_out must be after punch_in")
        if new_punch_out is not None:
            delta_hours = (new_punch_out - new_punch_in).total_seconds() / 3600.0
            if delta_hours > 24:
                raise HTTPException(status_code=422, detail="punch_out must be within 24 hours of punch_in")

    derived = normalize_record_values(employee, new_status, new_punch_in, new_punch_out)
    effective = {
        "status": new_status,
        "punch_in": derived["punch_in"],
        "punch_out": derived["punch_out"],
    }
    old_values = {k: record.get(k) for k in ["status", "punch_in", "punch_out"] if k in record}
    if old_values == effective:
        raise HTTPException(status_code=422, detail="request does not change the record")

    change_map = {}
    for field in ["status", "punch_in", "punch_out"]:
        old = old_values.get(field)
        new = effective.get(field)
        if old != new:
            change_map[field] = {"from": old, "to": new}
    for field in ["work_hours", "late_minutes", "overtime_minutes", "half_day"]:
        old = record.get(field, {
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        }[field])
        new = derived.get(field)
        if old != new:
            change_map[field] = {"from": old, "to": new}
    if not change_map:
        raise HTTPException(status_code=422, detail="request does not change the record")

    entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": payload.regularized_by,
        "reason": payload.reason,
        "changes": change_map,
    }
    update_doc = {
        "$set": {
            "status": new_status,
            "punch_in": derived["punch_in"],
            "punch_out": derived["punch_out"],
            "work_hours": derived["work_hours"],
            "late_minutes": derived["late_minutes"],
            "overtime_minutes": derived["overtime_minutes"],
            "half_day": derived["half_day"],
        },
        "$push": {"history": entry},
    }
    update_filter = {"_id": record["_id"]}
    update_filter["history"] = record["history"] if "history" in record else {"$exists": False}
    result = database.db.attendance_logs.update_one(update_filter, update_doc)
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="record was modified concurrently")
    updated = database.db.attendance_logs.find_one({"_id": record["_id"]})
    return serialize_record(updated)
