"""DLP_TIER2_MODEL points Tier 2 at a pinned local snapshot."""
import importlib

import pytest


@pytest.fixture(autouse=True)
def _restore_engine(monkeypatch):
    yield
    monkeypatch.undo()
    from app.pipeline import engine
    importlib.reload(engine)


def test_model_env_override(monkeypatch):
    monkeypatch.setenv("DLP_TIER2_MODEL", "/opt/doppel/model")
    monkeypatch.setenv("DLP_TIER2_ENABLED", "false")
    monkeypatch.setenv("DLP_VAULT_PERSIST", "false")
    from app.pipeline import engine
    engine = importlib.reload(engine)
    assert engine._tier2.config.model_name == "/opt/doppel/model"


def test_model_default_when_unset(monkeypatch):
    monkeypatch.delenv("DLP_TIER2_MODEL", raising=False)
    monkeypatch.setenv("DLP_TIER2_ENABLED", "false")
    monkeypatch.setenv("DLP_VAULT_PERSIST", "false")
    from app.pipeline import engine
    engine = importlib.reload(engine)
    assert engine._tier2.config.model_name.startswith("fastino/")
