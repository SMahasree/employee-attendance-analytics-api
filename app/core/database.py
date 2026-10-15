from typing import Any, Optional

from pymongo import MongoClient

from .config import settings

try:
    import mongomock
except ImportError:  # pragma: no cover
    mongomock = None

if settings.use_mock_mongo:
    if mongomock is None:
        raise RuntimeError("USE_MOCK_MONGO=1 requires mongomock; install requirements.txt")
    client = mongomock.MongoClient(tz_aware=True)
else:
    client = MongoClient(settings.mongo_uri, tz_aware=True)

db = client[settings.mongo_db]


def ensure_indexes(database: Optional[Any] = None) -> None:
    target = database if database is not None else db
    target.employees.create_index("emp_code", unique=True)
    target.employees.create_index([("department", 1), ("emp_code", 1)])
    target.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    target.attendance_logs.create_index([("emp_code", 1), ("punch_in", 1)])
    target.attendance_logs.create_index([("date", 1), ("emp_code", 1)])
    target.attendance_logs.create_index([("status", 1), ("date", 1)])


def close_client() -> None:
    client.close()
