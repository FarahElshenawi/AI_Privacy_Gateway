# ADR 0001: Gate fine-tuning behind a zero-shot benchmark

**Status:** Accepted — Week 1, v4 of the plan.

## Context

GLiNER2-PII ships already fine-tuned for PII detection by its maker and is
label-conditioned: the target entity schema is passed at inference, not baked
in by retraining. The original plan committed GPU provisioning and a full
ai4privacy fine-tuning pipeline in Week 1 before measuring whether that was
necessary.

## Decision

Week 1 now runs GLiNER2-PII zero-shot against `eval/eval_set_v1.json` (Role 1,
Tuesday) as a go/no-go gate, before committing further fine-tuning engineering
time. Fine-tuning only proceeds (Branch B) if the zero-shot result doesn't
clear the bar on precision/recall, including the hard-case subset.

## Consequences

- Role 1's and Role 5's Wednesday/Thursday tasks branch on this result. Both
  branches are hour-matched, so the Week 1 effort-fairness totals hold either way.
- Role 5's GPU provisioning (Monday) still happens regardless, as cheap
  insurance against GPU lead time — the only task that isn't gated.
- If the gate passes, Role 5's Cloud Backend work moves earlier as the default
  path instead of waiting on a fine-tuning track that may not happen.
