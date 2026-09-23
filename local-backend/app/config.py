"""Central config, read from env (see .env.example at repo root)."""
import os

LOCAL_BACKEND_PORT = int(os.getenv("LOCAL_BACKEND_PORT", 8765))
DEBUG_LOGGING_ENABLED = os.getenv("DEBUG_LOGGING_ENABLED", "false").lower() == "true"
VAULT_EXPIRY_HOURS = int(os.getenv("VAULT_EXPIRY_HOURS", 24))

# Fail-closed by construction: any pipeline exception should propagate to a
# blocked response, never a silent pass-through. See docs/architecture.md 1.4.
FAIL_CLOSED = True
