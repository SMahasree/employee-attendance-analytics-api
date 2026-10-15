import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    mongo_uri: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    mongo_db: str = os.getenv("MONGO_DB", "attendance_db")
    use_mock_mongo: bool = os.getenv("USE_MOCK_MONGO") == "1"


settings = Settings()
