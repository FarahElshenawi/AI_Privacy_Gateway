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

## 4. Policy Configuration & Overrides

The routing layer supports dynamic configuration overrides while guaranteeing built-in secure defaults.

### Flow:
```
Default Baseline Policy (ROUTING_TABLE)
             ↓
Configured Overrides (dict or JSON file)
             ↓
Validation & Security Invariant Checks
             ↓
Deterministic RoutingDecision
```

### Configuration Methods:
1. **Programmatic Override:**
   ```python
   from dlp_core.policy import configure_policy_override, Action, StoragePolicy

   # Override an existing entity type
   configure_policy_override("ORGANIZATION", Action.REDACT)

   # Configure custom entity with storage policy
   configure_policy_override("CUSTOM_TOKEN", "REDACT", storage="STORE_FOR_DEMASKING", risk_level="HIGH")
   ```

2. **From Dict / Structured Config:**
   ```python
   from dlp_core.policy import load_policy_config

   load_policy_config({
       "OVERRIDES": {
           "ORGANIZATION": "REDACT",
           "SENSITIVE_DATE": {"action": "KEEP"},
           "INTERNAL_URL": {"action": "REDACT", "storage": "NO_STORE"}
       }
   })
   ```

3. **From JSON File:**
   ```python
   from dlp_core.policy import load_policy_config_file

   load_policy_config_file("config/policy_overrides.json")
   ```

### Security Constraints:
* **Immutable Secrets:** High-risk credentials (`API_KEY`, `PASSWORD`, `JWT`, `PRIVATE_KEY`, `CLOUD_SECRET`, `CONNECTION_STRING`, `RECOVERY_CODE`, `CREDIT_CARD`, `CVV`) **cannot** be downgraded to `KEEP`. Attempting to do so raises `PolicyConfigError`.
* **Fail-Closed Unknowns:** Any unconfigured or unknown entity continues to fail-safe to `REDACT` + `NO_STORE`.
* **Resetting:** `reset_policy_to_defaults()` restores the engine back to factory default policies.

---

## 5. Routing Performance Benchmark

A reproducible, standalone benchmark is available at `benchmark_routing.py`. It measures pure routing engine latency and throughput without running external LLMs or detection models.

### How to Run:
```bash
cd local-backend
python benchmark_routing.py
```

### Workloads & Actual Measured Results:
Measured on standard x86_64 hardware:

| Workload | Total Operations | Total Time | Average Latency | P95 Latency | Throughput |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **10 Entities** | 1,000 | 4.99 ms | **4.61 µs** (0.0046 ms) | **9.20 µs** | **200,509 ops/sec** |
| **100 Entities** | 5,000 | 23.33 ms | **4.28 µs** (0.0042 ms) | **8.70 µs** | **214,316 ops/sec** |
| **1,000 Entities** | 10,000 | 47.55 ms | **4.43 µs** (0.0044 ms) | **8.70 µs** | **210,285 ops/sec** |
| **10,000 Entities** | 30,000 | 143.17 ms | **4.43 µs** (0.0044 ms) | **8.80 µs** | **209,537 ops/sec** |

*Conclusion:* Deterministic routing executes in **< 5 microseconds** per entity with throughput exceeding **200,000 operations/sec**, satisfying the sub-millisecond requirement.

---

## 6. Running the Unit Tests

```bash
cd local-backend

# Run all Role 4 test suites:
python -m pytest dlp_core/test_routing.py dlp_core/test_routing_storage.py dlp_core/test_policy_config.py -v
```
