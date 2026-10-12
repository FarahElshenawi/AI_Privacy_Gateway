"""DLP_TIER2_MODEL points Tier 2 at a pinned local snapshot.

Each case runs in a fresh interpreter: reloading app.pipeline.engine in-process would replace the
shared vault/masker that the API modules already hold and break later tests.
"""
import os
import subprocess
import sys


def _model_name(**env_overrides) -> str:
    env = {k: v for k, v in os.environ.items() if k != "DLP_TIER2_MODEL"}
    env.update(DLP_TIER2_ENABLED="false", DLP_VAULT_PERSIST="false", DLP_WARM_TIER2="false", **env_overrides)
    out = subprocess.run([sys.executable, "-c", "import app.pipeline.engine as e; print(e._tier2.config.model_name)"],
                         capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    return out.stdout.strip()


def test_model_env_override():
    assert _model_name(DLP_TIER2_MODEL="/opt/doppel/model") == "/opt/doppel/model"


def test_model_default_when_unset():
    assert _model_name().== "Hager290/gliner2_FT"
