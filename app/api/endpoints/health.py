from fastapi import APIRouter, HTTPException
from ...core import database

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    try:
        database.db.command("ping")
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok"}
