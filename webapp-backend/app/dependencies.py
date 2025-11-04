from fastapi import Request
from typing import Optional
import redis
import os
import uuid
import json

from app.models import User

# Redis client
_redis_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    """Get Redis client singleton."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=0,
            decode_responses=True,
        )
    return _redis_client


def get_redis_client() -> redis.Redis:
    """Alias for dependency compatibility."""
    return get_redis()


# Session store in Redis
def get_or_create_session(request: Request) -> dict:
    """Get or create user session - permissive mode."""
    redis_client = get_redis()

    # Try to get session from cookie
    session_id = request.cookies.get("session_id")

    if session_id:
        # Try to load existing session
        session_key = f"session:{session_id}"
        session_data = redis_client.get(session_key)

        if session_data:
            return json.loads(session_data)

    # Create new session (auto-login for demo)
    session_id = str(uuid.uuid4())
    user_id = abs(hash(session_id)) % 1_000_000  # Generate consistent user_id

    session = {
        "session_id": session_id,
        "user_id": user_id,
        "username": f"Player{user_id}",
        "created_at": str(uuid.uuid4()),
    }

    # Store in Redis with 24h expiry
    session_key = f"session:{session_id}"
    redis_client.setex(session_key, 86400, json.dumps(session))

    return session


async def get_current_user(request: Request) -> User:
    """Extract current user information from the session."""
    session = get_or_create_session(request)
    return User(id=session["user_id"], username=session.get("username"))
