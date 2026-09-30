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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.detect import router as detect_router
from app.api.mask import router as mask_router
from app.api.demask import router as demask_router
from app.api.process_file import router as file_router
from app.security.auth import get_install_token

app = FastAPI(
    title="PII Gateway Local Backend",
    description="Local PII detection, masking, and restoration service.",
    version="1.0.0",
)

# CORS — only allow the extension (which runs on chat.openai.com)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Wire routers
app.include_router(detect_router, prefix="/api", tags=["detection"])
app.include_router(mask_router, prefix="/api", tags=["masking"])
app.include_router(demask_router, prefix="/api", tags=["restoration"])
app.include_router(file_router, prefix="/api", tags=["multimodal"])


@app.get("/health")
async def health():
    """Health check — no auth required."""
    return {"status": "ok", "service": "pii-gateway-backend", "version": "1.0.0"}


@app.get("/token")
async def get_token():
    """Get the install token for extension setup.

    This endpoint is unauthenticated because the extension needs to
    get the token before it can make authenticated requests.
    In production, this would be restricted to localhost only.
    """
    return {"token": get_install_token()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
