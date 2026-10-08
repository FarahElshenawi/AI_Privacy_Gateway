"""FastAPI entry point for the Cloud Control Plane.

Wires together the policy and audit routers, initializes the database,
seeds default policies, and exposes a health check.

Run with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

Environment:
    DATABASE_URL         database URL (default: sqlite:///./cloud_backend.db)
    CLOUD_ADMIN_API_KEY  API key of the default organization (>= 24 chars). If unset on first
                         boot, one is generated and printed once. Setting it later rotates the key.
    CORS_ORIGINS         comma-separated dashboard origins (default: local Vite dev servers)
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.db import init_db, seed_defaults
from app.api import policy, audit, endpoints

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize the database and seed defaults on first boot."""
    seed_defaults(init_db())
    yield


def create_app() -> FastAPI:
    """Application factory — wires routers + middleware + startup hooks."""
    app = FastAPI(
        title="Doppel Cloud Control Plane",
        description=(
            "Policy distribution + audit collection for the AI Privacy Gateway. "
            "Carries metadata only (entity types, counts, timing) — never prompt content."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS — the dashboard (and nothing else) may call us from a browser. Authentication is a
    # header, not a cookie, so credentials are never needed; origins default to local dev only.
    allowed_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Key", "Authorization", "Content-Type"],
    )

    # Wire routers (every route in them requires an API key)
    app.include_router(policy.router)
    app.include_router(audit.router)
    app.include_router(endpoints.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        return {"status": "ok", "service": "cloud-control-plane", "version": "1.0.0"}

    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        """Bare URL → /health so visiting http://localhost:8000/ works in a browser."""
        return RedirectResponse(url="/health")

    return app


app = create_app()
