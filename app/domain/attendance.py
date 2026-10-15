import re
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from fastapi import HTTPException

IST = timezone(timedelta(hours=5, minutes=30))
PRESENCE_STATUSES = {"PRESENT", "WFH", "ON_DUTY"}


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
