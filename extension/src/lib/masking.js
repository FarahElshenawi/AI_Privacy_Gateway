// Masking functions consumed behind one interface (docs/architecture.md 1.2, Step 4).
// faker()  -> synthetic substitution (PERSON, EMAIL, PHONE, ORGANIZATION if decision-routed)
// redact() -> hard block (CREDIT_CARD, API_KEY, credentials)
// context()-> anchor + description (ORGANIZATION fallback path — see routing table caveat)
//
// Owner: Role 3. Unit tests: extension/tests/masking.test.js

export function faker(entity) { /* TODO */ }
export function redact(entity) { /* TODO */ }
export function context(entity) { /* TODO */ }
