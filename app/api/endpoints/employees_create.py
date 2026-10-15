from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pymongo.errors import DuplicateKeyError

from ...core import database
from ...domain.attendance import serialize_employee
from ...schemas import EmployeeCreate

router = APIRouter()


@router.post("/employees", status_code=201)
def create_employee(body: EmployeeCreate) -> dict[str, Any]:
    if database.db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(status_code=409, detail="emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        result = database.db.employees.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="emp_code already exists") from exc
    stored = database.db.employees.find_one({"_id": result.inserted_id})
    return serialize_employee(stored)
