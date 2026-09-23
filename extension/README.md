# Extension — Data Plane, Enforcement Point 1

Chrome extension (Manifest V3). Owns the MAIN-world `fetch()` override on chatgpt.com —
the highest-variance component in the system (see docs/development-plan.md, Week 1, Role 3).

**Security note (docs/architecture.md 1.4):** this code runs in the page's JS context,
which is untrusted. It must never hold real PII or the Vault — it only forwards prompts
to the Local Backend and swaps in the masked body it gets back.
