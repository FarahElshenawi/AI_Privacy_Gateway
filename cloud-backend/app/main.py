"""Cloud Backend — FastAPI application for the Control Plane.

Wires policy and audit routers. Handles DB initialization on startup.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db, seed_defaults
from app.api.policy import router as policy_router
from app.api.audit import router as audit_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB + seed defaults on startup
    engine = init_db()
    seed_defaults(engine)
    yield


app = FastAPI(
    title="AI Privacy Gateway — Cloud Backend",
    description="Control plane: policy distribution and audit ingestion. Metadata only — never prompt content.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow the dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Wire routers
app.include_router(policy_router, tags=["policies"])
app.include_router(audit_router, tags=["audit"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pii-gateway-cloud", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
