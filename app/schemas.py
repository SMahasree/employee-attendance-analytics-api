from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .domain.attendance import PRESENCE_STATUSES, parse_date_str, parse_hhmm

EPOCH_MILLIS = Annotated[int, Field(strict=True, ge=100000000000, le=4102444800000)]
STATUS = Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]

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
