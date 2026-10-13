import os
import uuid
from urllib.parse import urlsplit

TEST_MONGO_URI = os.getenv("ATTENDANCE_TEST_MONGO_URI")
TEST_DATABASE = f"attendance_test_{uuid.uuid4().hex}"

if TEST_MONGO_URI:
    hostname = urlsplit(TEST_MONGO_URI).hostname
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("ATTENDANCE_TEST_MONGO_URI must point to a local MongoDB instance")
    os.environ.pop("USE_MOCK_MONGO", None)
    os.environ["MONGO_URI"] = TEST_MONGO_URI
    os.environ["MONGO_DB"] = TEST_DATABASE
else:
    os.environ["USE_MOCK_MONGO"] = "1"
