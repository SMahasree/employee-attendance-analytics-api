from typing import Any, Optional

from fastapi import APIRouter, Query

from ...core import database
from ...domain.attendance import serialize_employee

router = APIRouter()


@router.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if department is not None:
        query["department"] = department
    total = database.db.employees.count_documents(query)
    items = list(database.db.employees.find(query, {"_id": 0}).sort("emp_code", 1).skip((page - 1) * page_size).limit(page_size))
    return {"items": [serialize_employee(item) for item in items], "total": total, "page": page, "page_size": page_size}
