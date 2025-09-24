from fastapi import APIRouter, Header, HTTPException
from ...core.config import settings


router = APIRouter()


def _check_key(x_service_role_key: str | None):
    expected = settings.INTERNAL_API_KEY
    if not expected or x_service_role_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/health")
def internal_health(x_service_role_key: str | None = Header(None)):
    _check_key(x_service_role_key)
    return {"status": "ok"}

