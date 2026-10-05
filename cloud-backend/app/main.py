"""FastAPI entry point for the Cloud Control Plane.

Wires together the policy and audit routers, initializes the database,
seeds default policies, and exposes a health check.

Run with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.db import init_db, seed_defaults
from app.api import policy, audit


def create_app() -> FastAPI:
    """Application factory — wires routers + middleware + startup hooks."""
    app = FastAPI(
        title="Doppel Cloud Control Plane",
        description=(
            "Policy distribution + audit collection for the AI Privacy Gateway. "
            "Carries metadata only (entity types, counts, timing) — never prompt content."
        ),
        version="1.0.0",
    )

    # CORS — allow the dashboard (and any local backend) to call us.
    # In production, restrict ORIGIN to the dashboard's domain.
    allowed_origins = os.getenv("CORS_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in allowed_origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Wire routers
    app.include_router(policy.router)
    app.include_router(audit.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        return {"status": "ok", "service": "cloud-control-plane", "version": "1.0.0"}

    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        """Bare URL → /health so visiting http://localhost:8000/ works in a browser."""
        return RedirectResponse(url="/health")

    @app.on_event("startup")
    def _startup() -> None:
        """Initialize the database and seed defaults on first boot."""
        engine = init_db()
        seed_defaults(engine)

    return app


app = create_app()
