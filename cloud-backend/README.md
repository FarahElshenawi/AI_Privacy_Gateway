# Cloud Backend — Control Plane

FastAPI service. Policy distribution + audit collection. Runs on a $5 VPS.
Owner: Role 5. See docs/architecture.md 1.1 and 1.4 (Cloud Audit Defaults).

**Default is metadata-only audit** (entity types, counts, hashes — never full
text). Full-text capture (CP2/CP3) is an enterprise opt-in only.
