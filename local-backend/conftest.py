"""Pytest configuration — ensures `app` package is importable from tests.

Disables vault persistence by default so tests don't pollute ~/.pii_gateway_vault.db.
Tests that need persistence explicitly set DLP_VAULT_PERSIST=true in their own setup
or use a temp DLP_VAULT_DB_PATH.
"""
import sys
import os

# Tests must NOT write to the user's real ~/.pii_gateway_vault.db — set this BEFORE
# any app module import so engine.py reads the env var at import time.
os.environ.setdefault("DLP_VAULT_PERSIST", "false")
# The model is not installed in CI; strict file mode (the production default) would reject every file.
os.environ.setdefault("DLP_FILE_STRICT", "false")

sys.path.insert(0, os.path.dirname(__file__))
