# AI Privacy Gateway — Detection Layer Evaluation

This document captures the full evaluation journey — three datasets, four detection configurations, and the final architecture decision for the AI Privacy Gateway's PII detection layer.

---

## Overview

The AI Privacy Gateway intercepts enterprise prompts before they reach external LLMs (ChatGPT, Claude, Z.ai) and masks confidential data in real time. The detection layer is the foundation of the entire system — if detection misses, the whole masking pipeline is bypassed.

After three rounds of evaluation on progressively realistic datasets, the architecture settled on **three complementary detection layers** merged by a routing table:

```
Prompt → SpanMarker → GLiNER → Regex/Checksum → Merge → Masking → LLM
```

Each layer catches what the others miss.

---

## The 3 Detection Layers

| Layer                | Model                                                | Size              | Strength                                                    | Weakness                                                  |
| -------------------- | ---------------------------------------------------- | ----------------- | ----------------------------------------------------------- | --------------------------------------------------------- |
| **SpanMarker**       | `tomaarsen/span-marker-bert-base-fewnerd-fine-super` | 111M              | PERSON names (F1 0.98)                                      | Cannot detect credentials, IDs, financial PII             |
| **GLiNER2-PII**      | `urchade/gliner_multi_pii-v1`                        | 300M              | Semantic PII (label-conditioned, 33 types)                  | Brittle on digit-heavy entities (PHONE, CVV)              |
| **Regex + Checksum** | Custom Python patterns                               | 0 (deterministic) | Near-perfect on checksum entities (Luhn, mod-97, SSN rules) | Brittle when formats vary (PHONE_NUMBER regex too greedy) |

**Layer 4 (Combined):** Merges all three using a routing table that specifies which model is primary for each entity type. Currently has known bugs — see [Known Issues](#known-issues-and-fixes).

---

## Dataset Evolution

Three datasets were evaluated, each more realistic than the last:

| Dataset                        | Examples | Entity types | Total entities | Description                                                              |
| ------------------------------ | -------- | ------------ | -------------- | ------------------------------------------------------------------------ |
| **D1** `eval_set.jsonl`        | 80       | 17           | 278            | General NER (included ORG, MONEY, TIME, etc. — not all PII)              |
| **D2** `eval_set_v3.jsonl`     | 300      | 17           | ~1200          | PII-focused (dropped non-PII; added credentials, IDs, financial)         |
| **D3** `pii_ner_dataset.jsonl` | 400      | 33           | 1659           | Full PII taxonomy (added health, government IDs, enterprise identifiers) |

### Why the progression mattered

- **D1 → D2:** Removed non-PII entities (ORGANIZATION, MONEY, TIME) that were inflating F1 scores without testing actual PII detection
- **D2 → D3:** Expanded from 17 to 33 entity types per the project's PII taxonomy PDF — added credentials (CLOUD_SECRET, RECOVERY_CODE), government IDs (PASSPORT_NUMBER, DRIVER_LICENSE, TAX_ID), financial (SWIFT_BIC, ABA_ROUTING, CRYPTO_WALLET), and enterprise identifiers (INTERNAL_HOSTNAME, INTERNAL_URL, PROJECT_CODE_NAME)

---

## Full F1 Results

### Dataset 1: General NER (17 types)

`eval_set.jsonl` — 80 examples, 278 entities, mixed PII and general entities

| Entity          | Sup | SpanMarker | GLiNER    | Regex     | Winner |
| --------------- | --- | ---------- | --------- | --------- | ------ |
| PERSON          | 64  | **0.893**  | 0.794     | 0.000     | SM     |
| ORGANIZATION    | 53  | 0.804      | **0.873** | 0.000     | GL     |
| DATE            | 33  | 0.000      | **0.921** | 0.000     | GL     |
| LOCATION        | 33  | **0.806**  | 0.769     | 0.000     | SM     |
| MONEY           | 19  | 0.000      | **0.950** | 0.000     | GL     |
| EMAIL           | 9   | 0.000      | **0.750** | 0.000     | GL     |
| ID_NUMBER       | 6   | 0.000      | **0.286** | 0.000     | GL     |
| IP_ADDRESS      | 6   | 0.000      | 0.500     | **0.909** | RX     |
| TIME            | 6   | 0.000      | **0.632** | 0.000     | GL     |
| USERNAME        | 6   | 0.000      | **0.909** | 0.333     | GL     |
| PRODUCT         | 5   | 0.667      | **0.769** | 0.000     | GL     |
| FAC             | 4   | **0.889**  | 0.286     | 0.000     | SM     |
| LANGUAGE        | 4   | **1.000**  | 0.800     | 0.000     | SM     |
| LAW             | 4   | 0.667      | 0.667     | 0.000     | Tie    |
| PERCENT         | 4   | 0.000      | **1.000** | 0.000     | GL     |
| URL             | 4   | 0.000      | 0.600     | **0.857** | RX     |
| EVENT           | 3   | 0.333      | 0.267     | 0.000     | SM     |
| NATIONALITY     | 3   | 0.000      | **0.571** | 0.000     | GL     |
| BANK_ACCOUNT    | 2   | 0.000      | **0.667** | 0.000     | GL     |
| ORDINAL         | 2   | 0.000      | 0.000     | 0.000     | None   |
| PHONE_NUMBER    | 2   | 0.000      | **1.000** | 0.400     | GL     |
| WORK_OF_ART     | 2   | 0.667      | 0.667     | 0.000     | Tie    |
| AGE             | 1   | 0.000      | **1.000** | 0.000     | GL     |
| CREDIT_CARD     | 1   | 0.000      | **1.000** | **1.000** | Tie    |
| PASSPORT_NUMBER | 1   | 0.000      | 0.000     | 0.000     | None   |
| QUANTITY        | 1   | 0.000      | **1.000** | 0.000     | GL     |

**Win counts:** SM = 5, GL = 14, RX = 2, Tie = 3, None = 2

---

### Dataset 2: PII-Focused (17 types)

`eval_set_v3.jsonl` — 300 examples, ~1200 entities, real-world LLM prompt paragraphs

| Entity               | Sup | SpanMarker | GLiNER    | Regex     | Winner |
| -------------------- | --- | ---------- | --------- | --------- | ------ |
| PERSON               | 210 | **0.986**  | 0.966     | 0.000     | SM     |
| API_KEY              | 106 | 0.000      | **0.612** | 0.000     | GL     |
| EGYPTIAN_NATIONAL_ID | 87  | 0.000      | 0.000     | **1.000** | RX     |
| TICKET_ID            | 84  | 0.000      | 0.656     | **0.957** | RX     |
| PHONE_NUMBER         | 72  | 0.000      | **1.000** | 0.667     | GL     |
| CONNECTION_STRING    | 69  | 0.000      | 0.000     | **1.000** | RX     |
| PASSWORD             | 68  | 0.000      | **0.576** | 0.000     | GL     |
| EMAIL                | 67  | 0.000      | 0.924     | **1.000** | RX     |
| POSTAL_ADDRESS       | 57  | 0.000      | **0.973** | 0.459     | GL     |
| US_SSN               | 56  | 0.000      | 0.000     | **1.000** | RX     |
| EMPLOYEE_ID          | 55  | 0.000      | 0.843     | **1.000** | RX     |
| AUTH_TOKEN           | 54  | 0.000      | 0.229     | **0.667** | RX     |
| CREDIT_CARD          | 54  | 0.000      | **0.981** | 0.887     | GL     |
| PRIVATE_KEY          | 49  | 0.000      | 0.000     | **0.805** | RX     |
| CVV                  | 45  | 0.000      | 0.875     | **0.916** | RX     |
| DATE_OF_BIRTH        | 45  | 0.000      | **1.000** | **1.000** | Tie    |
| IBAN                 | 32  | 0.000      | **1.000** | 0.815     | GL     |

**Win counts:** SM = 1, GL = 7, RX = 8, Tie = 1

---

### Dataset 3: Full PII Taxonomy (33 types)

`pii_ner_dataset.jsonl` — 400 examples, 1659 entities, the full PII taxonomy

| Entity               | Sup | SpanMarker | GLiNER    | Regex     | Winner |
| -------------------- | --- | ---------- | --------- | --------- | ------ |
| PERSON               | 90  | **0.983**  | 0.796     | 0.000     | SM     |
| TICKET_ID            | 85  | 0.000      | 0.508     | **0.775** | RX     |
| PASSWORD             | 69  | 0.000      | 0.599     | **0.681** | RX     |
| INTERNAL_URL         | 67  | 0.000      | **0.356** | 0.205     | GL     |
| EMAIL                | 66  | 0.000      | 0.680     | **1.000** | RX     |
| PROJECT_CODE_NAME    | 62  | 0.000      | 0.680     | **0.819** | RX     |
| IP_ADDRESS           | 61  | 0.000      | 0.609     | **0.626** | RX     |
| PHONE_NUMBER         | 57  | 0.000      | **0.991** | 0.026     | GL     |
| USERNAME             | 57  | 0.000      | **0.688** | 0.413     | GL     |
| EMPLOYEE_ID          | 56  | 0.000      | **1.000** | 0.809     | GL     |
| CLOUD_SECRET         | 54  | 0.000      | 0.000     | **0.265** | RX     |
| POSTAL_ADDRESS       | 54  | 0.000      | **0.951** | 0.119     | GL     |
| CONNECTION_STRING    | 52  | 0.000      | 0.000     | **0.920** | RX     |
| EGYPTIAN_NATIONAL_ID | 51  | 0.000      | **0.776** | 0.000     | GL     |
| API_KEY              | 50  | 0.000      | **0.651** | 0.000     | GL     |
| US_SSN               | 50  | 0.000      | 0.990     | **1.000** | RX     |
| AUTH_TOKEN           | 49  | 0.000      | 0.317     | **0.886** | RX     |
| RECOVERY_CODE        | 49  | 0.000      | 0.340     | **0.356** | RX     |
| DATE_OF_BIRTH        | 48  | 0.000      | **1.000** | **1.000** | Tie    |
| PRIVATE_KEY          | 48  | 0.000      | 0.000     | **0.843** | RX     |
| CREDIT_CARD          | 47  | 0.000      | **0.854** | 0.000     | GL     |
| INTERNAL_HOSTNAME    | 46  | 0.000      | 0.676     | **0.713** | RX     |
| BANK_ACCOUNT_NUMBER  | 45  | 0.000      | **0.918** | 0.000     | GL     |
| CRYPTO_WALLET        | 40  | 0.000      | 0.367     | **1.000** | RX     |
| ABA_ROUTING          | 39  | 0.000      | **0.975** | 0.000     | GL     |
| IBAN                 | 36  | 0.000      | 0.714     | **0.800** | RX     |
| TAX_ID               | 36  | 0.000      | **0.958** | 0.737     | GL     |
| SWIFT_BIC            | 35  | 0.000      | 0.921     | **1.000** | RX     |
| CARD_EXPIRY          | 34  | 0.000      | **0.865** | 0.000     | GL     |
| CVV                  | 34  | 0.000      | **0.600** | 0.000     | GL     |
| PASSPORT_NUMBER      | 34  | 0.000      | **0.958** | 0.636     | GL     |
| DRIVER_LICENSE       | 29  | 0.000      | **0.863** | 0.000     | GL     |
| NATIONAL_ID_OTHER    | 29  | 0.000      | 0.459     | **0.651** | RX     |

**Win counts:** SM = 1, GL = 13, RX = 12, Tie = 1

---

## Cross-Dataset Trends

Entities that appear in multiple datasets, showing how each model evolved as the dataset got more realistic:

| Entity               | D1 → D2 → D3           | Trend                                         |
| -------------------- | ---------------------- | --------------------------------------------- |
| PERSON               | SM: 0.89 → 0.99 → 0.98 | ✅ Stable, SpanMarker's specialty             |
| EMAIL                | RX: 0.00 → 1.00 → 1.00 | ✅ Regex nailed it after D1                   |
| PHONE_NUMBER         | GL: 1.00 → 1.00 → 0.99 | ✅ GLiNER consistent                          |
| US_SSN               | RX: – → 1.00 → 1.00    | ✅ Regex perfect (when format is consistent)  |
| EGYPTIAN_NATIONAL_ID | RX: – → 1.00 → 0.00    | ⚠️ Brittle — format changed between D2 and D3 |
| CONNECTION_STRING    | RX: – → 1.00 → 0.92    | ✅ Regex strong                               |
| CREDIT_CARD          | GL: 1.00 → 0.98 → 0.85 | ⚠️ GLiNER degrading as dataset gets harder    |
| CVV                  | RX: – → 0.92 → 0.00    | ⚠️ Brittle — D3 broke the regex pattern       |
| DATE_OF_BIRTH        | GL: 0.92 → 1.00 → 1.00 | ✅ GLiNER perfect                             |
| IBAN                 | GL: – → 1.00 → 0.71    | ⚠️ GLiNER dropped on D3                       |

### Three patterns visible in the data

1. **SpanMarker is consistent** at PERSON (0.89–0.99) and useless everywhere else — its FewNERD training doesn't cover PII
2. **GLiNER degrades** as datasets get more realistic — more entities to distinguish means more confusion (EMAIL: 0.92 → 0.68; CVV: 0.88 → 0.60)
3. **Regex is brittle** — perfect when formats match (US_SSN, IBAN, SWIFT), but breaks when formats vary (EGYPTIAN_NATIONAL_ID: 1.00 → 0.00)

---

## Final Architecture Decision

Based on all three rounds of evaluation, the production pipeline should use **all three layers** with this routing:

### SpanMarker wins (use SpanMarker as primary)

| Entity | Best F1 | Notes                                                           |
| ------ | ------- | --------------------------------------------------------------- |
| PERSON | 0.983   | SpanMarker's FewNERD specialty — consistent across all datasets |

### GLiNER wins (use GLiNER as primary)

| Entity              | Best F1 | Notes                                     |
| ------------------- | ------- | ----------------------------------------- |
| PHONE_NUMBER        | 0.991   | GLiNER's strongest semantic PII           |
| DATE_OF_BIRTH       | 1.000   | Perfect on D2 and D3                      |
| POSTAL_ADDRESS      | 0.951   | Strong on D3                              |
| PASSPORT_NUMBER     | 0.958   | Strong on D3                              |
| TAX_ID              | 0.958   | Strong on D3                              |
| EMPLOYEE_ID         | 1.000   | Perfect on D3                             |
| DRIVER_LICENSE      | 0.863   | Strong on D3                              |
| CREDIT_CARD         | 0.854   | Better than regex on D3                   |
| ABA_ROUTING         | 0.975   | Strong on D3                              |
| BANK_ACCOUNT_NUMBER | 0.918   | Strong on D3                              |
| CARD_EXPIRY         | 0.865   | GLiNER only model that detects this       |
| CVV                 | 0.600   | GLiNER only model that detects this on D3 |
| USERNAME            | 0.688   | Slight edge over regex                    |
| API_KEY             | 0.651   | GLiNER only model that detects this       |
| INTERNAL_URL        | 0.356   | Low — needs improvement                   |

### Regex wins (use Regex as primary)

| Entity               | Best F1 | Notes                                      |
| -------------------- | ------- | ------------------------------------------ |
| US_SSN               | 1.000   | Perfect with area/group/serial rules       |
| EGYPTIAN_NATIONAL_ID | 1.000   | Perfect on D2 (broke on D3 — format issue) |
| IBAN                 | 0.800   | Strong with mod-97 checksum                |
| SWIFT_BIC            | 1.000   | Perfect with format check                  |
| CRYPTO_WALLET        | 1.000   | Perfect on D3                              |
| CONNECTION_STRING    | 0.920   | URI scheme patterns                        |
| EMAIL                | 1.000   | RFC pattern                                |
| PRIVATE_KEY          | 0.843   | PEM header/footer                          |
| AUTH_TOKEN           | 0.886   | JWT shape + Bearer                         |
| TICKET_ID            | 0.775   | Jira-style patterns                        |
| INTERNAL_HOSTNAME    | 0.713   | Domain patterns                            |
| IP_ADDRESS           | 0.626   | IPv4/IPv6/MAC                              |
| PROJECT_CODE_NAME    | 0.819   | "Project X" pattern                        |
| PASSWORD             | 0.681   | Keyword + value                            |
| CLOUD_SECRET         | 0.265   | Weak — needs improvement                   |
| RECOVERY_CODE        | 0.356   | Weak — needs improvement                   |
| NATIONAL_ID_OTHER    | 0.651   | Multi-format                               |
