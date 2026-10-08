"""FastAPI application — PII Gateway local backend.

Wires all routers together:
  - /detect     — detect PII in text
  - /mask       — mask PII in text (detect + mask + scan)
  - /demask     — restore real values in LLM response
  - /process_file — process a file upload (multimodal)
  - /health     — health check
  - /token      — get the install token (for extension setup)
"""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.detect import router as detect_router
from app.api.mask import router as mask_router
from app.api.demask import router as demask_router
from app.api.process_file import router as file_router
from app.pipeline.engine import tier_status, warm_tier2
from app.security.auth import get_install_token
from app.security.origin_check import check_origin
from app import cloud_sync

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load the semantic model in the background: the server answers immediately and Tier 2 is
    # reported as "warming_up" (degraded coverage) until it is ready, instead of the first
    # user request waiting for the model load.
    threading.Thread(target=warm_tier2, name="tier2-warmup", daemon=True).start()
    # Start cloud sync (enrollment, heartbeat, audit push, policy pull).
    # No-op if CLOUD_URL or CLOUD_API_KEY is unset — runs standalone.
    cloud_sync.start()
    try:
        yield
    finally:
        cloud_sync.stop()


app = FastAPI(
    lifespan=lifespan,
    title="PII Gateway Local Backend",
    description="Local PII detection, masking, and restoration service.",
    version="1.0.0",
)

# CORS — ONLY browser extensions. Web pages (chatgpt.com, any site) must never be able to read
# responses from this backend: that would let page script fetch /token and then call /api/demask
# to turn surrogate names back into real ones. The extension's service worker has host
# permission for this origin and is exempt from CORS anyway; the regex just also covers
# extension pages. `check_origin` independently rejects any non-extension Origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=r"^chrome-extension://[a-p]{32}$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Wire routers
app.include_router(detect_router, prefix="/api", tags=["detection"])
app.include_router(mask_router, prefix="/api", tags=["masking"])
app.include_router(demask_router, prefix="/api", tags=["restoration"])
app.include_router(file_router, prefix="/api", tags=["multimodal"])


@app.get("/health")
async def health():
    """Health check — no auth required. Reports tier readiness as short codes (no user data)."""
    return {
        "status": "ok",
        "service": "pii-gateway-backend",
        "version": "1.0.0",
        "tiers": tier_status(),
        "cloud_sync": cloud_sync.status(),
    }


@app.get("/token")
async def get_token(request: Request):
    """Get the install token for extension setup.

    This endpoint is unauthenticated because the extension needs to
    get the token before it can make authenticated requests.
    In production, this would be restricted to localhost only.
    """
    check_origin(request)      # loopback clients only (the full extension handshake is a separate task)
    return {"token": get_install_token()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
