"""API settings and per-request database access.

The engine is created once in the app lifespan (module-level connections are
banned); routes borrow a short-lived Connection via dependency injection.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import HTTPException, Request
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from risk.db import DEFAULT_DB_URL


class Settings(BaseSettings):
    """DATABASE_URL from the environment or .env (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = DEFAULT_DB_URL
    # comma-separated allowed origins for the browser dashboard; the default
    # covers a local Vite dev server, the hosted origin is set on Render
    api_cors_origins: str = "http://localhost:5173"
    model_doc_path: str = "docs/model_doc.md"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def get_conn(request: Request) -> Iterator[Connection]:
    # connect-time failures become a 503 inside the exception middleware, where
    # CORS headers still apply - a raw 500 would bypass CORSMiddleware and show
    # the browser an opaque CORS error instead of "database unreachable"
    try:
        cm = request.app.state.engine.connect()
    except OperationalError as exc:
        raise HTTPException(status_code=503,
                            detail=f"database unreachable: {type(exc).__name__}") from exc
    with cm as conn:
        yield conn
