"""Tier 1 configuration. Frozen, so one engine instance is safe to share across threads."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class Tier1Config:
    # Hosts/URLs ending in these are internal infrastructure.
    internal_suffixes: tuple[str, ...] = ("internal", "intranet", "corp", "lan", "local",
                                          "localdomain", "home.arpa", "cluster.local")
    # Customer-owned internal-only domains, e.g. ("corp.acme.com",): every *.domain host is internal.
    tenant_domains: tuple[str, ...] = ()
    # Customer-specific words that must never leave (project names, client names).
    deny_terms: tuple[str, ...] = ()
    ignore_loopback_ips: bool = True          # 127.0.0.1, ::1, 0.0.0.0 are harmless in code pastes
    private_ip_label: str = "IP_ADDRESS"      # "INTERNAL_HOSTNAME" to treat RFC1918 as infrastructure
    # Phones: cue-anchored by default (Tier 2 owns uncued numbers). True also emits strongly
    # formatted uncued phones (+E.164 / NANP with separators) at MODEL evidence.
    emit_uncued_phones: bool = False
    enabled_labels: Optional[frozenset[str]] = None   # None = every Tier 1 label
    max_text_len: int = 2_000_000             # callers chunk larger inputs

    @property
    def all_internal_suffixes(self) -> tuple[str, ...]:
        return tuple(sorted({s.lower().lstrip(".") for s in (*self.internal_suffixes, *self.tenant_domains)}))
