"""Explicit configuration; no implicit production credentials or provider calls."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql://cip:cip@127.0.0.1:55432/cip"
    redis_url: str = "redis://127.0.0.1:56379/0"
    lease_seconds: int = 60
    dispatch_seconds: int = 10
    max_upload_bytes: int = 512_000

    @classmethod
    def load(cls) -> "Settings":
        if os.getenv("CIP_ENV") == "production" and any(
            not os.getenv(key) for key in ("DATABASE_URL", "REDIS_URL")
        ):
            raise ValueError("production_requires_explicit_database_and_broker_configuration")
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            redis_url=os.getenv("REDIS_URL", cls.redis_url),
        )
