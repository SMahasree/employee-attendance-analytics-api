# uvicorn app.main:app --port 8000

import os
import re
from calendar import monthrange
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Any, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

try:
    import mongomock
except ImportError:  # pragma: no cover
    mongomock = None

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))
PRESENCE_STATUSES = {"PRESENT", "WFH", "ON_DUTY"}
EPOCH_MILLIS = Annotated[int, Field(strict=True, ge=100000000000, le=4102444800000)]
STATUS = Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]


def round_half_up(value: float, digits: int) -> float:
    quant = Decimal("1").scaleb(-digits)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def round_percentage_half_up(present_days: float, working_days: int) -> float:
    percentage = Decimal(str(present_days)) * Decimal(100) / Decimal(working_days)
    return float(percentage.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, dict) and "$date" in value:
        date_value = value["$date"]
        if isinstance(date_value, datetime):
            return date_value
        if isinstance(date_value, str):
            return datetime.fromisoformat(date_value.replace("Z", "+00:00"))
    return value


def epoch_ms_to_datetime(value: Optional[int]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromtimestamp(value // 1000, tz=timezone.utc)


def truncate_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)


def datetime_to_epoch_ms(value: Optional[Any]) -> Optional[int]:
    if value is None:
        return None
    value = coerce_datetime(value)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return int(value.timestamp()) * 1000


def parse_date_str(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must use YYYY-MM-DD")
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def get_shift_context_for_datetime(emp: dict[str, Any], instant_utc: datetime) -> dict[str, Any]:
    local = instant_utc.astimezone(IST)
    start_hhmm = parse_hhmm(emp["shift_start"])
    end_hhmm = parse_hhmm(emp["shift_end"])
    shift_start_dt = datetime.combine(local.date(), start_hhmm, tzinfo=IST)
    shift_end_dt = datetime.combine(local.date(), end_hhmm, tzinfo=IST)
    if end_hhmm <= start_hhmm:
        if local.time() < end_hhmm:
            shift_start_dt = datetime.combine(local.date() - timedelta(days=1), start_hhmm, tzinfo=IST)
            shift_end_dt = datetime.combine(local.date(), end_hhmm, tzinfo=IST)
        else:
            shift_start_dt = datetime.combine(local.date(), start_hhmm, tzinfo=IST)
            shift_end_dt = datetime.combine(local.date() + timedelta(days=1), end_hhmm, tzinfo=IST)
    else:
        shift_start_dt = datetime.combine(local.date(), start_hhmm, tzinfo=IST)
        shift_end_dt = datetime.combine(local.date(), end_hhmm, tzinfo=IST)

    record_date = shift_start_dt.date().isoformat()
    if end_hhmm <= start_hhmm and local.time() < end_hhmm:
        record_date = (local.date() - timedelta(days=1)).isoformat()
    return {
        "record_date": record_date,
        "shift_start_ist": shift_start_dt,
        "shift_end_ist": shift_end_dt,
        "local": local,
    }


def get_shift_context_for_record_date(emp: dict[str, Any], record_date: str) -> dict[str, Any]:
    shift_date = parse_date_str(record_date)
    start_hhmm = parse_hhmm(emp["shift_start"])
    end_hhmm = parse_hhmm(emp["shift_end"])
    shift_start = datetime.combine(shift_date, start_hhmm, tzinfo=IST)
    shift_end = datetime.combine(shift_date, end_hhmm, tzinfo=IST)
    if end_hhmm <= start_hhmm:
        shift_end += timedelta(days=1)
    return {"shift_start_ist": shift_start, "shift_end_ist": shift_end}


def compute_late_minutes(emp: dict[str, Any], punch_in_utc: datetime) -> int:
    ctx = get_shift_context_for_datetime(emp, punch_in_utc)
    delta_seconds = int((punch_in_utc.astimezone(IST) - ctx["shift_start_ist"]).total_seconds())
    return delta_seconds // 60 if delta_seconds > 600 else 0


def compute_work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    seconds = Decimal(str((punch_out_utc - punch_in_utc).total_seconds()))
    return float((seconds / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime_minutes(emp: dict[str, Any], punch_out_utc: datetime, punch_in_utc: datetime) -> int:
    attendance_date = get_shift_context_for_datetime(emp, punch_in_utc)["record_date"]
    ctx = get_shift_context_for_record_date(emp, attendance_date)
    delta_seconds = int((punch_out_utc.astimezone(IST) - ctx["shift_end_ist"]).total_seconds())
    if delta_seconds < 1800:
        return 0
    return delta_seconds // 60


def compute_half_day(work_hours: Optional[float]) -> bool:
    if work_hours is None:
        return False
    return round_half_up(work_hours, 2) < 4.5


def normalize_record_values(emp: dict[str, Any], status: str, punch_in: Optional[datetime], punch_out: Optional[datetime]) -> dict[str, Any]:
    punch_in = truncate_datetime(punch_in)
    punch_out = truncate_datetime(punch_out)
    normalized = {
        "status": status,
        "punch_in": punch_in,
        "punch_out": punch_out,
        "late_minutes": 0,
        "overtime_minutes": 0,
        "work_hours": None,
        "half_day": False,
    }
    if status in PRESENCE_STATUSES:
        if punch_in is not None:
            normalized["late_minutes"] = compute_late_minutes(emp, punch_in)
        if punch_in is not None and punch_out is not None:
            normalized["work_hours"] = compute_work_hours(punch_in, punch_out)
            normalized["overtime_minutes"] = compute_overtime_minutes(emp, punch_out, punch_in)
            normalized["half_day"] = compute_half_day(normalized["work_hours"])
    return normalized


def serialize_record(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    out.pop("_id", None)
    out.setdefault("punch_in", None)
    out.setdefault("punch_out", None)
    out.setdefault("work_hours", None)
    out.setdefault("late_minutes", 0)
    out.setdefault("overtime_minutes", 0)
    out.setdefault("half_day", False)
    out.setdefault("history", [])
    for key in ["punch_in", "punch_out"]:
        if key in out and out[key] is not None:
            out[key] = datetime_to_epoch_ms(out[key])
    if "created_at" in out and out["created_at"] is not None and not isinstance(out["created_at"], int):
        out["created_at"] = datetime_to_epoch_ms(out["created_at"])
    if "history" in out:
        rebuilt = []
        for item in out.get("history") or []:
            h = dict(item)
            h["at"] = datetime_to_epoch_ms(h.get("at"))
            changes = {}
            for field, val in (h.get("changes") or {}).items():
                changes[field] = {
                    "from": datetime_to_epoch_ms(val.get("from")) if field in {"punch_in", "punch_out"} else val.get("from"),
                    "to": datetime_to_epoch_ms(val.get("to")) if field in {"punch_in", "punch_out"} else val.get("to"),
                }
            h["changes"] = changes
            rebuilt.append(h)
        out["history"] = rebuilt
    return out


def serialize_employee(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    out.pop("_id", None)
    if "created_at" in out and out["created_at"] is not None:
        out["created_at"] = datetime_to_epoch_ms(out["created_at"])
    return out


def compute_month_range(month: str) -> tuple[str, str]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(status_code=422, detail="month must use YYYY-MM")
    try:
        start = datetime.strptime(month, "%Y-%m").date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="month must be a valid YYYY-MM month") from exc
    month_end = start.replace(day=monthrange(start.year, start.month)[1])
    return start.strftime("%Y-%m-%d"), month_end.strftime("%Y-%m-%d")


def validate_date_parameter(value: str, name: str) -> date:
    try:
        return parse_date_str(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name} must be a valid YYYY-MM-DD date") from exc


def month_working_days(joined_on: str, month: str) -> int:
    start_day, end_day = compute_month_range(month)
    joined = parse_date_str(joined_on)
    start = max(joined, parse_date_str(start_day))
    count = 0
    current = start
    while current <= parse_date_str(end_day):
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


if os.getenv("USE_MOCK_MONGO") == "1":
    if mongomock is None:
        raise RuntimeError("USE_MOCK_MONGO=1 requires mongomock; install requirements.txt")
    client = mongomock.MongoClient(tz_aware=True)
else:
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)

db = client[os.getenv("MONGO_DB", "attendance_db")]


def ensure_indexes() -> None:
    db.employees.create_index("emp_code", unique=True)
    db.employees.create_index([("department", 1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("emp_code", 1), ("punch_in", 1)])
    db.attendance_logs.create_index([("date", 1), ("emp_code", 1)])
    db.attendance_logs.create_index([("status", 1), ("date", 1)])

class EmployeeCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str = Field(..., pattern=r"^EMP[0-9]{4,6}$")
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not value or "@" not in value:
            raise ValueError("invalid email")
        local, domain = value.rsplit("@", 1)
        if not local or not domain or "." not in domain:
            raise ValueError("invalid email")
        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        parse_date_str(value)
        return value

    @model_validator(mode="after")
    def validate_shift(self):
        if parse_hhmm(self.shift_start) == parse_hhmm(self.shift_end):
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[EPOCH_MILLIS] = None
    status: str = Field(default="PRESENT")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in PRESENCE_STATUSES:
            raise ValueError("status must be one of PRESENT, WFH, ON_DUTY")
        return value

    @model_validator(mode="after")
    def reject_null_timestamp(self):
        if "punched_at" in self.model_fields_set and self.punched_at is None:
            raise ValueError("punched_at must be an epoch-millisecond integer")
        return self


class PunchOutRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[EPOCH_MILLIS] = None

    @model_validator(mode="after")
    def reject_null_timestamp(self):
        if "punched_at" in self.model_fields_set and self.punched_at is None:
            raise ValueError("punched_at must be an epoch-millisecond integer")
        return self


class RegularizeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Optional[STATUS] = None
    punch_in: Optional[EPOCH_MILLIS] = None
    punch_out: Optional[EPOCH_MILLIS] = None
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)

    @model_validator(mode="after")
    def ensure_has_change(self):
        if any(name in self.model_fields_set and getattr(self, name) is None for name in ("status", "punch_in", "punch_out")):
            raise ValueError("status and punch times cannot be null")
        if self.status is None and self.punch_in is None and self.punch_out is None:
            raise ValueError("no fields supplied to change")
        return self


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        ensure_indexes()
        yield
    finally:
        client.close()


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    try:
        db.command("ping")
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeCreate) -> dict[str, Any]:
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(status_code=409, detail="emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        result = db.employees.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="emp_code already exists") from exc
    stored = db.employees.find_one({"_id": result.inserted_id})
    return serialize_employee(stored)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if department is not None:
        query["department"] = department
    total = db.employees.count_documents(query)
    items = list(db.employees.find(query, {"_id": 0}).sort("emp_code", 1).skip((page - 1) * page_size).limit(page_size))
    return {"items": [serialize_employee(item) for item in items], "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInRequest) -> dict[str, Any]:
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="employee not found")
    punch_dt = epoch_ms_to_datetime(body.punched_at) or datetime.now(timezone.utc).replace(microsecond=0)
    record_date = get_shift_context_for_datetime(emp, punch_dt)["record_date"]
    existing = db.attendance_logs.find_one({"emp_code": body.emp_code, "date": record_date})
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
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="already punched in for this date") from exc
    return serialize_record(db.attendance_logs.find_one({"emp_code": body.emp_code, "date": record_date}))


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutRequest) -> dict[str, Any]:
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="employee not found")
    punch_dt = epoch_ms_to_datetime(body.punched_at) or datetime.now(timezone.utc).replace(microsecond=0)
    record = db.attendance_logs.find_one(
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
    result = db.attendance_logs.update_one({"_id": record["_id"], "punch_out": None}, update)
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="record already punched out")
    doc = db.attendance_logs.find_one({"_id": record["_id"]})
    return serialize_record(doc)


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


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    query = attendance_filter(emp_code, date_from, date_to, status)
    total = db.attendance_logs.count_documents(query)
    docs = list(db.attendance_logs.find(query).sort([("date", -1), ("emp_code", 1)]).skip((page - 1) * page_size).limit(page_size))
    return {"items": [serialize_record(item) for item in docs], "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, payload: RegularizeRequest) -> dict[str, Any]:
    validate_date_parameter(date, "date")
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
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
        old = record.get(field)
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
    result = db.attendance_logs.update_one(update_filter, update_doc)
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="record was modified concurrently")
    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    return serialize_record(updated)


def employee_monthly_pipeline(emp_code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": start_date, "$lte": end_date}}},
        {"$addFields": {"date_obj": {"$dateFromString": {"dateString": "$date", "timezone": "Asia/Kolkata"}}}},
        {"$group": {
            "_id": None,
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$in": [{"$dayOfWeek": {"date": "$date_obj", "timezone": "Asia/Kolkata"}}, [2, 3, 4, 5, 6]]},
                        ]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                        0,
                    ]
                }
            },
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "total_overtime_minutes": {"$sum": "$overtime_minutes"},
        }},
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")) -> dict[str, Any]:
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    start_date, end_date = compute_month_range(month)
    result = list(db.attendance_logs.aggregate(employee_monthly_pipeline(emp_code, start_date, end_date)))
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


def department_summary_pipeline(start_date: str, end_date: str, department: Optional[str]) -> list[dict[str, Any]]:
    pipeline = [
        {"$match": {"date": {"$gte": start_date, "$lte": end_date}}},
        {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend([
        {"$addFields": {"date_obj": {"$dateFromString": {"dateString": "$date", "timezone": "Asia/Kolkata"}}}},
        {"$group": {
            "_id": "$employee.department",
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$in": [{"$dayOfWeek": {"date": "$date_obj", "timezone": "Asia/Kolkata"}}, [2, 3, 4, 5, 6]]},
                        ]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                        0,
                    ]
                }
            },
            "avg_work_hours": {"$avg": {"$cond": [{"$and": [{"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}, {"$ne": ["$work_hours", None]}]}, "$work_hours", None]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ])
    return pipeline


@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), department: Optional[str] = None) -> dict[str, Any]:
    start_date, end_date = compute_month_range(month)
    pipeline = department_summary_pipeline(start_date, end_date, department)
    rows = list(db.attendance_logs.aggregate(pipeline))
    if department is None:
        department_names = sorted(db.employees.distinct("department", {"joined_on": {"$lte": end_date}}))
    else:
        department_names = [department] if db.employees.count_documents({"department": department, "joined_on": {"$lte": end_date}}) else []
    results = []
    for dept in department_names:
        headcount = db.employees.count_documents({"department": dept, "joined_on": {"$lte": end_date}})
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


def late_leaderboard_pipeline(
    start_date: str,
    end_date: str,
    department: Optional[str],
    limit: int,
) -> list[dict[str, Any]]:
    pipeline = [
        {"$match": {"date": {"$gte": start_date, "$lte": end_date}}},
        {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend([
        {"$group": {
            "_id": "$emp_code",
            "name": {"$first": "$employee.name"},
            "department": {"$first": "$employee.department"},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
        }},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}},
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {
            "_id": 0,
            "emp_code": "$_id",
            "name": 1,
            "department": 1,
            "total_late_minutes": 1,
            "late_count": 1,
            "rank": 1,
        }},
    ])
    return pipeline


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), limit: int = Query(10, ge=1, le=50), department: Optional[str] = None) -> dict[str, Any]:
    start_date, end_date = compute_month_range(month)
    pipeline = late_leaderboard_pipeline(start_date, end_date, department, limit)
    return {"month": month, "items": list(db.attendance_logs.aggregate(pipeline))}


def department_trend_pipeline(department: str, from_date: str, day_diff: int) -> list[dict[str, Any]]:
    return [
        {"$match": {"department": department}},
        {"$group": {"_id": None, "employees": {"$push": {"emp_code": "$emp_code", "joined_on": "$joined_on"}}}},
        {"$project": {
            "_id": 0,
            "employees": 1,
            "dates": {"$map": {
                "input": {"$range": [0, day_diff]},
                "as": "offset",
                "in": {"$dateToString": {
                    "format": "%Y-%m-%d",
                    "date": {"$dateAdd": {
                        "startDate": {"$dateFromString": {"dateString": from_date, "timezone": "Asia/Kolkata"}},
                        "unit": "day",
                        "amount": "$$offset",
                    }},
                    "timezone": "Asia/Kolkata",
                }},
            }},
        }},
        {"$unwind": "$dates"},
        {"$set": {
            "date": "$dates",
            "eligible_employees": {"$filter": {
                "input": "$employees",
                "as": "employee",
                "cond": {"$lte": ["$$employee.joined_on", "$dates"]},
            }},
        }},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {
                "day": "$date",
                "employee_codes": {"$map": {
                    "input": "$employees",
                    "as": "employee",
                    "in": "$$employee.emp_code",
                }},
            },
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$date", "$$day"]},
                    {"$in": ["$emp_code", "$$employee_codes"]},
                ]}}},
                {"$group": {
                    "_id": None,
                    "present_count": {"$sum": {"$cond": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                        0,
                    ]}},
                    "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
                }},
            ],
            "as": "daily",
        }},
        {"$set": {
            "headcount": {"$size": "$eligible_employees"},
            "present_count": {"$ifNull": [{"$arrayElemAt": ["$daily.present_count", 0]}, 0]},
            "late_count": {"$ifNull": [{"$arrayElemAt": ["$daily.late_count", 0]}, 0]},
            "is_working_day": {"$in": [
                {"$dayOfWeek": {
                    "date": {"$dateFromString": {"dateString": "$date", "timezone": "Asia/Kolkata"}},
                    "timezone": "Asia/Kolkata",
                }},
                [2, 3, 4, 5, 6],
            ]},
        }},
        {"$set": {"attendance_rate": {"$cond": [
            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            {"$divide": [
                {"$floor": {"$add": [
                    {"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 1000000]},
                    0.5,
                ]}},
                10000,
            ]},
            None,
        ]}}},
        {"$sort": {"date": 1}},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {"moving_avg_7d": {
                "$avg": "$attendance_rate",
                "window": {"documents": [-6, 0]},
            }},
        }},
        {"$project": {
            "_id": 0,
            "date": 1,
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": {"$cond": [
                {"$ne": ["$moving_avg_7d", None]},
                {"$divide": [
                    {"$floor": {"$add": [{"$multiply": ["$moving_avg_7d", 10000]}, 0.5]}},
                    10000,
                ]},
                None,
            ]},
        }},
        {"$sort": {"date": 1}},
    ]


@app.get("/analytics/departments/{department}/trend")
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
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(status_code=404, detail="department not found")
    pipeline = department_trend_pipeline(department, from_date, day_diff)
    return {"department": department, "items": list(db.employees.aggregate(pipeline))}


@app.get("/admin/explain/{endpoint}")
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
        explain = db.command("explain", {
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
        explain = db.command("explain", {
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
        explain = db.command("explain", {
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
        explain = db.command("explain", {
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
        if db.employees.count_documents({"department": department}) == 0:
            raise HTTPException(status_code=404, detail="department not found")
        pipeline = department_trend_pipeline(department, from_date, day_diff)
        explain = db.command("explain", {
            "aggregate": "employees",
            "pipeline": pipeline,
            "cursor": {},
        }, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "employees", "explain": explain}
    raise HTTPException(status_code=404, detail="unknown endpoint")
