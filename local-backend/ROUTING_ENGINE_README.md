# Role 4 — Policy & Deterministic Routing Engine (New Architecture)

This document describes the migrated Role 4 Policy & Deterministic Routing Engine integrated directly into `dlp_core` within the new architecture.

---

## 1. Architecture & Pipeline Location

The routing engine operates deterministically between the detection tiers and the offset-based masking engine:

```
DetectionPipeline (Tier 1 + Tier 2)
              ↓
          Span list
              ↓
         MergeEngine
              ↓
      MergedSpan sequence
              ↓
OffsetMasker + Deterministic Policy (dlp_core/policy.py + dlp_core/masker.py)
              ↓
  Audit Logs (dlp_core/audit.py)  +  Sealed Vault (dlp_core/vault.py)
              ↓
       Residual Scanner
              ↓
   Safe Output to External LLM
```

---

## 2. Decoupled Policy Concepts

The engine enforces three strictly decoupled dimensions:
1. **Policy Action (WHAT happens):**
   - `KEEP`: Value remains untouched in the clear.
   - `FAKER` / `MASK`: Replaced with a realistic surrogate stand-in.
   - `REDACT`: Replaced with a redaction placeholder.
   - `BLOCK`: Aborts processing immediately; raises `RequestBlockedError`.
2. **Processing Strategy (HOW it is transformed):**
   - `PSEUDONYMIZE` (for `FAKER`)
   - `REDACTION` (for `REDACT`)
   - `NONE` (for `KEEP`)
   - `REJECTION` (for `BLOCK`)
3. **Storage Policy (WHETHER the original is retained):**
   - `STORE_FOR_DEMASKING`: Kept in the encrypted `InMemoryVault` for post-response restoration.
   - `NO_STORE`: Zero retention in the vault; irreversible.

---

## 3. Entity Routing Matrix

| Entity Type | Policy Action | Processing Strategy | Storage Policy | Replacement | Stored in Vault? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **PERSON**, **ORGANIZATION**, **LOCATION**, **EMAIL**, **PHONE_NUMBER**, **PHONE_E164**, **ADDRESS**, **USERNAME**, **DATE_OF_BIRTH** | `FAKER` | `PSEUDONYMIZE` | `STORE_FOR_DEMASKING` | Synthetic surrogate | **Yes** |
| **CREDIT_CARD**, **CVV**, **CARD_EXPIRY**, **BANK_ACCOUNT_NUMBER**, **US_SSN**, **TAX_ID**, **MEDICAL_RECORD_NUMBER**, **HEALTH_INSURANCE_ID**, **GOVERNMENT_ID**, **PASSPORT_NUMBER**, **DRIVERS_LICENSE_NUMBER**, **ACCOUNT_ID** | `REDACT` | `REDACTION` | `NO_STORE` | `[REDACTED:LABEL]` | **No** |
| **API_KEY**, **AUTH_TOKEN**, **JWT**, **PRIVATE_KEY**, **PEM_BLOCK**, **CLOUD_SECRET**, **SECRET**, **CONNECTION_STRING**, **PASSWORD**, **RECOVERY_CODE**, **DENY_TERM** | `REDACT` | `REDACTION` | `NO_STORE` | `[REDACTED:LABEL]` | **No** |
| **IBAN**, **ABA_ROUTING**, **SWIFT_BIC**, **CRYPTO_WALLET**, **INTERNAL_URL**, **INTERNAL_HOSTNAME** | `REDACT` | `REDACTION` | `STORE_FOR_DEMASKING` | `[[REDACTED:LABEL:xxxx]]` | **Yes** |
| **URL** | `KEEP` | `NONE` | `NO_STORE` | Unchanged | **No** |
| **IP_ADDRESS (Public / Globally Routable)** | `KEEP` | `NONE` | `NO_STORE` | Unchanged | **No** |
| **IP_ADDRESS (Private / Loopback / CGNAT / Unparseable)** | `REDACT` | `REDACTION` | `NO_STORE` | `[REDACTED:IP_ADDRESS]` | **No** |
| **\<Unknown Entity\>** (Fail-closed) | `REDACT` | `REDACTION` | `NO_STORE` | `[REDACTED:LABEL]` | **No** |

---

## 4. Key Security & Architecture Invariants

1. **Deterministic Offline IP Classification:**
   Evaluates IP values using Python's standard-library `ipaddress` module against IANA registries (RFC 6890 / 8190). Zero network lookups or external DNS.
2. **Bijective Redaction Demasking:**
   Entities configured as `REDACT` + `STORE_FOR_DEMASKING` receive unique random-token placeholders (e.g. `[[REDACTED:IBAN:de4e]]`). This ensures multiple distinct entries (like two IBANs) never collide in the bijective vault and can be cleanly restored by `Demasker`.
3. **Zero-Leakage Audit Logging:**
   `dlp_core/audit.py` records metadata only (`entity_type`, `action`, `strategy`, `storage`, `risk_level`, `entity_category`, `rule`, `conversation_id`, `value_length`). It never logs the original secret, substring, hash, or generated replacement token.
4. **Immediate BLOCK Abort:**
   Any `BLOCK` entity immediately raises `RequestBlockedError`, stopping execution before any vault write or token emission occurs.

---

## 5. Running the Tests

To run the complete Role 4 test suite in the new repository:

```bash
cd local-backend
python -m pytest dlp_core/test_routing.py dlp_core/test_routing_storage.py -v
```
