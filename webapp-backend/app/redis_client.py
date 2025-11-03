import redis
import os
from typing import Optional

# استفاده از Redis client سازگار با ورژن 4.5.4
redis_client: Optional[redis.Redis] = None

def get_redis_client() -> redis.Redis:
    """Get Redis client instance (singleton pattern)."""
    global redis_client
    
    if redis_client is None:
        redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True
        )
    
    return redis_client
