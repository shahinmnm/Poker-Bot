import os
from typing import Optional


class Config:
    def __init__(self) -> None:
        self.TOKEN: str = os.getenv("POKERBOT_TOKEN", "")
        self.REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
        self.REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
        self.REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
        self.REDIS_PASS: Optional[str] = os.getenv("REDIS_PASSWORD", None)
        self.DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

        # PTB 20+ specific settings
        self.CONCURRENT_UPDATES: int = int(
            os.getenv("CONCURRENT_UPDATES", "256")
        )
        self.CONNECT_TIMEOUT: int = int(os.getenv("CONNECT_TIMEOUT", "30"))
        self.POOL_TIMEOUT: int = int(os.getenv("POOL_TIMEOUT", "30"))
