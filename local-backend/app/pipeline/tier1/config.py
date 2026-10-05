from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EngineConfig:
    # Hostnames/URLs ending in these are treated as internal infrastructure.
    internal_suffixes: tuple = ("internal", "intranet", "corp", "lan", "local", "localdomain",
                                "home.arpa", "cluster.local")
    # Tenant-supplied INTERNAL-ONLY domains (every *.domain host is internal), e.g. ("corp.acme.com",).
    tenant_domains: tuple = ()
    ignore_loopback_ips: bool = True       # 127.0.0.1, ::1, 0.0.0.0 are harmless in code pastes
    private_ip_label: str = "IP_ADDRESS"   # set to "INTERNAL_HOSTNAME" to route RFC1918 IPs as infrastructure
    enabled_labels: Optional[frozenset] = None   # None = every Tier 1 label
    max_text_len: int = 2_000_000          # callers chunk larger inputs (per page / per sheet)

    @property
    def all_internal_suffixes(self) -> tuple:
        return tuple(sorted({s.lower().lstrip(".") for s in (*self.internal_suffixes, *self.tenant_domains)}))
