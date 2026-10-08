"""Contract tests between cloud-backend defaults and local-backend deterministic routing engine.

Verifies:
1. Exact 1:1 match of canonical entity set.
2. Exact match of default actions for every canonical entity.
3. Complete exclusion of aliases and value-dependent IP labels.
4. Clear failure reporting if either side drifts.
"""
from pathlib import Path
import importlib.util
import pytest

from dlp_core.policy import ROUTING_TABLE, _IP_LABELS, ALIASES


def _load_cloud_catalog():
    defaults_path = Path(__file__).resolve().parents[2] / "cloud-backend" / "app" / "defaults.py"
    assert defaults_path.is_file(), f"Cloud backend defaults file not found at {defaults_path}"
    
    spec = importlib.util.spec_from_file_location("cloud_defaults", defaults_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "CANONICAL_DEFAULT_POLICIES")


def test_contract_canonical_entity_set_matches():
    cloud_catalog = _load_cloud_catalog()
    core_canonical = set(ROUTING_TABLE.keys())
    cloud_canonical = set(cloud_catalog.keys())

    missing_in_cloud = core_canonical - cloud_canonical
    extra_in_cloud = cloud_canonical - core_canonical

    assert not missing_in_cloud, (
        f"Catalog drift: Entities present in dlp_core.policy.ROUTING_TABLE but missing in cloud-backend: {sorted(missing_in_cloud)}"
    )
    assert not extra_in_cloud, (
        f"Catalog drift: Entities present in cloud-backend but missing in dlp_core.policy.ROUTING_TABLE: {sorted(extra_in_cloud)}"
    )


def test_contract_default_actions_match():
    cloud_catalog = _load_cloud_catalog()

    mismatches = []
    for label, entry in ROUTING_TABLE.items():
        expected_action = entry.action.name.lower()
        actual_action = cloud_catalog.get(label)
        if actual_action != expected_action:
            mismatches.append(f"{label}: cloud={actual_action} vs core={expected_action}")

    assert not mismatches, (
        f"Catalog drift: Default action mismatches between cloud-backend and dlp_core: {', '.join(mismatches)}"
    )


def test_contract_no_aliases_or_ip_labels_in_cloud_catalog():
    cloud_catalog = _load_cloud_catalog()

    for alias in ALIASES.keys():
        assert alias not in cloud_catalog, (
            f"Catalog violation: Alias '{alias}' must NOT be in cloud-backend canonical catalog"
        )

    for ip_label in _IP_LABELS:
        assert ip_label not in cloud_catalog, (
            f"Catalog violation: Value-dependent IP label '{ip_label}' must NOT be in cloud-backend static catalog"
        )
