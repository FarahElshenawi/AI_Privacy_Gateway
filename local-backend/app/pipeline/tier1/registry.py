"""Registry: the single place where patterns, validators, scores and context rules are wired.

Adding or tuning an entity means editing one block here (and nothing in the engine).
Scores are precision priors, not probabilities; the merge engine's calibration step
should replace them with per-label values measured on the held-out set.
"""
from __future__ import annotations

import re

from . import patterns as P
from . import validators as V
from .config import EngineConfig
from .recognizers import (AwsSecretRecognizer, CardRecognizer, IbanRecognizer, PatternRecognizer,
                          RecoveryCodeRecognizer)


def _generic_secret_label(m: re.Match) -> str:
    return "CLOUD_SECRET" if "secret" in m.group("k").lower() else "API_KEY"


def _signature_recognizers():
    for name, rx, min_entropy in P.API_KEY_SIGNATURES:
        yield PatternRecognizer(
            name=f"sig_{name}", labels=("API_KEY",), pattern=re.compile(rx),
            validator=(lambda v, me=min_entropy: V.shannon_entropy(v) >= me),
            hard_evidence=True, score=0.98,
        )


def build_default_recognizers(cfg: EngineConfig) -> list:
    suffixes = cfg.all_internal_suffixes
    ignore_lo = cfg.ignore_loopback_ips
    R = PatternRecognizer
    recs: list = []

    # ---- payment cards (PCI-DSS) --------------------------------------------------------
    recs += [
        CardRecognizer(),
        R("cvv_keyword", ("CVV",), P.CVV_KEYWORD, group="v", score=0.95),
        R("expiry_keyword", ("CARD_EXPIRY",), P.EXPIRY_KEYWORD, group="v",
          validator=V.expiry_valid, score=0.95),
    ]

    # ---- bank identifiers ---------------------------------------------------------------
    recs += [
        IbanRecognizer(),
        R("swift_bic", ("SWIFT_BIC",), P.SWIFT, validator=V.swift_valid, hard_evidence=False,
          score=0.9, context=P.SWIFT_CTX, context_after=P.SWIFT_CTX, before=60, after=30),
        R("aba_routing", ("ABA_ROUTING",), P.ABA_NINE, validator=lambda v: V.aba_valid(v),
          hard_evidence=True, score=0.95, context=P.ABA_CTX_BEFORE, context_after=P.ABA_CTX_AFTER,
          context_required=True, before=40, after=24),
        R("bank_account", ("BANK_ACCOUNT_NUMBER",), P.BANK_ACCOUNT, group="v",
          validator=V.bank_account_valid, score=0.85),
        R("crypto_btc", ("CRYPTO_WALLET",), P.CRYPTO_BTC, validator=V.base58check_valid,
          hard_evidence=True, score=0.98),
        R("crypto_bech32", ("CRYPTO_WALLET",), P.CRYPTO_BECH32, validator=V.bech32_valid,
          hard_evidence=True, score=0.98),
        R("crypto_eth", ("CRYPTO_WALLET",), P.CRYPTO_ETH, score=0.8),
    ]

    # ---- government, tax, health identifiers ----------------------------------------------
    recs += [
        R("ssn_dashed", ("US_SSN",), P.SSN_DASHED, validator=V.ssn_valid, hard_evidence=True, score=0.93),
        R("ssn_bare", ("US_SSN",), P.SSN_BARE, validator=V.ssn_valid, hard_evidence=True, score=0.93,
          context=P.SSN_CTX_BEFORE, context_required=True, before=40),
        R("tax_id", ("TAX_ID",), P.TAX_ID, group="v", validator=V.tax_id_valid, score=0.85),
        R("mrn", ("MEDICAL_RECORD_NUMBER",), P.MRN, group="v", validator=V.mrn_valid, score=0.85),
        R("health_insurance", ("HEALTH_INSURANCE_ID",), P.HEALTH_INS, group="v", validator=V.mrn_valid, score=0.8),
    ]

    # ---- contact --------------------------------------------------------------------------
    recs += [
        R("email", ("EMAIL",), P.EMAIL, validator=V.email_valid, score=0.99),
        # PHONE_NUMBER is Tier 2 primary; these are lower-scored cross-check candidates.
        R("phone_e164", ("PHONE_NUMBER",), P.PHONE_E164, validator=V.e164_plausible, score=0.8),
        R("phone_nanp", ("PHONE_NUMBER",), P.PHONE_NANP, validator=V.nanp_valid, score=0.8),
        R("phone_bare10", ("PHONE_NUMBER",), P.PHONE_BARE10, validator=V.nanp_valid, score=0.75,
          context=P.PHONE_CTX_BEFORE, context_required=True, before=24),
    ]

    # ---- network and infrastructure -------------------------------------------------------
    recs += [
        R("ipv4", ("IP_ADDRESS", cfg.private_ip_label), P.IPV4,
          label_fn=lambda m: cfg.private_ip_label if V.ip_is_private(m.group()) else "IP_ADDRESS",
          validator=lambda v: V.ipv4_valid(v, ignore_lo), score=0.95),
        R("ipv6", ("IP_ADDRESS",), P.IPV6, validator=lambda v: V.ipv6_valid(v, ignore_lo), score=0.9),
        R("internal_url", ("INTERNAL_URL",), P.URL_ANY, trim_url=True,
          validator=lambda v: V.internal_url_valid(v, suffixes), score=0.9),
        R("internal_hostname", ("INTERNAL_HOSTNAME",), P.build_internal_host_pattern(suffixes),
          validator=lambda v: V.bare_internal_hostname_valid(v, suffixes), score=0.85),
    ]

    # ---- credentials and secrets (SOC 2 CC6) ----------------------------------------------
    recs += list(_signature_recognizers())
    recs += [
        R("jwt", ("AUTH_TOKEN",), P.JWT, validator=V.jwt_valid, hard_evidence=True, score=0.98),
        R("bearer", ("AUTH_TOKEN",), P.BEARER, group="v",
          validator=lambda v: V.secret_value_ok(v, 2.8, 16), score=0.9),
        R("basic_auth", ("AUTH_TOKEN",), P.BASIC_AUTH, group="v", validator=V.basic_auth_valid, score=0.92),
        R("google_oauth", ("AUTH_TOKEN",), P.GOOGLE_OAUTH, score=0.95),
        R("pem_private_key", ("PRIVATE_KEY",), P.PEM_FULL, validator=V.pem_valid, hard_evidence=True, score=0.99),
        R("pem_truncated", ("PRIVATE_KEY",), P.PEM_TRUNCATED, validator=V.pem_valid, score=0.9),
        R("connection_uri", ("CONNECTION_STRING",), P.CONN_URI, trim_url=True,
          validator=V.connection_string_valid, score=0.93),
        R("connection_kv", ("CONNECTION_STRING",), P.CONN_KV, score=0.9),
        R("password_kv", ("PASSWORD",), P.PASSWORD, group="v", validator=V.password_value_ok, score=0.88),
        R("aws_secret_kv", ("CLOUD_SECRET",), P.AWS_SECRET_KV, group="v",
          validator=lambda v: V.secret_value_ok(v, 3.5, 40), score=0.95),
        R("azure_secret_kv", ("CLOUD_SECRET",), P.AZURE_SECRET_KV, group="v",
          validator=lambda v: V.secret_value_ok(v, 3.0, 20), score=0.92),
        R("generic_secret_kv", ("API_KEY", "CLOUD_SECRET"), P.GENERIC_SECRET_KV, group="v",
          label_fn=_generic_secret_label, validator=lambda v: V.secret_value_ok(v, 3.2, 16), score=0.8),
        AwsSecretRecognizer(),
        RecoveryCodeRecognizer(),
    ]

    if cfg.enabled_labels is not None:
        recs = [r for r in recs if set(r.labels) & cfg.enabled_labels]
    return recs
