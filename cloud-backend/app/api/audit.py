"""Audit ingestion. Defaults to metadata-only (docs/architecture.md 1.4):
entity types, counts, hashes. Full-text (CP2/CP3) only if
CLOUD_AUDIT_MODE=full_text is explicitly set (enterprise opt-in)."""
from fastapi import APIRouter
router = APIRouter()
# TODO
