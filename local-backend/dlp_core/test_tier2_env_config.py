"""Tier 2 config: DLP_TIER2_MODEL and DLP_TIER2_EXTRA_LABELS override the defaults; unset changes nothing.

Each case runs in a fresh interpreter because the defaults are read when the config module is imported.
"""
import os
import subprocess
import sys
from pathlib import Path

from dlp_core.tier2.config import DEFAULT_LABELS

ROOT = Path(__file__).resolve().parents[1]          # local-backend/
ORIGINAL_MODEL = "fastino/gliner2-privacy-filter-PII-multi"


def _config(**env):
    code = "from dlp_core.tier2 import Tier2Config; c = Tier2Config(); print(c.model_name); print(','.join(c.labels))"
    full = {k: v for k, v in os.environ.items() if not k.startswith("DLP_TIER2_MODEL") and not k.startswith("DLP_TIER2_EXTRA")}
    full.update(env)
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full, capture_output=True, text=True, check=True)
    model, labels = out.stdout.strip().split("\n")
    return model, tuple(labels.split(","))


def test_defaults_are_unchanged_when_nothing_is_set():
    model, labels = _config()
    assert model == ORIGINAL_MODEL and labels == DEFAULT_LABELS


def test_model_can_be_overridden():
    model, labels = _config(DLP_TIER2_MODEL="someone/other-model")
    assert model == "someone/other-model" and labels == DEFAULT_LABELS


def test_extra_labels_are_appended_lowercased_without_duplicates():
    _, labels = _config(DLP_TIER2_EXTRA_LABELS=" Organization , phone_number,person,, ORGANIZATION")
    assert labels == DEFAULT_LABELS + ("organization", "phone_number")


def test_blank_extra_labels_change_nothing():
    _, labels = _config(DLP_TIER2_EXTRA_LABELS="  , ,")
    assert labels == DEFAULT_LABELS
