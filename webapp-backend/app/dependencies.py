"""Shared dependency functions for FastAPI routes."""

from typing import Dict, Optional

from fastapi import Depends, Header, HTTPException

from app.redis_client import get_redis_client as _get_redis_client
from app.utils import verify_session_token


def get_session_token(authorization: Optional[str] = Header(default=None, convert_underscores=False)) -> str:
    """Extract bearer token from the Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    return authorization.split(" ", 1)[1].strip()


def get_current_user(token: str = Depends(get_session_token)) -> Dict[str, str]:
    """Validate the incoming bearer token and return the session payload."""
    session = verify_session_token(token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return session


def get_redis_client():
    """Return the shared Redis client instance."""
    return _get_redis_client()
