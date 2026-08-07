"""API settings and per-request database access.

The engine is created once in the app lifespan (module-level connections are
banned); routes borrow a short-lived Connection via dependency injection.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import Request
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import Connection

from risk.db import DEFAULT_DB_URL


class Settings(BaseSettings):
    """DATABASE_URL from the environment or .env (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = DEFAULT_DB_URL


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def get_conn(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.connect() as conn:
        yield conn
