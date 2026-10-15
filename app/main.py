"""ASGI application entry point. Run with ``uvicorn app.main:app``."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.routes import router
from .core.database import close_client, db, ensure_indexes


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        ensure_indexes(db)
        yield
    finally:
        close_client()


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)
app.include_router(router)
