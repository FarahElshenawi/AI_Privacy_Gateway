# Evaluation Package

`eval_set_v1.json` — the 50-prompt hand-built evaluation set (Role 1), used for:

- **Metric 1** — Precision/Recall (scoring/precision_recall.py)
- **Metric 2** — Independent Recall via the residual scanner, not GLiNER (scoring/residual_scanner_eval.py)
- **Metric 3** — Semantic Fidelity, composite + hard-case subset reported separately (scoring/semantic_fidelity.py)
- The **Week 1 zero-shot gate** (docs/adr/0001-zero-shot-before-finetuning.md) also scores against this set.

10 inflection + 5 all-caps hard cases are tagged in `hard_case` per prompt — see
docs/architecture.md 1.3 (Vault) and the eval set's own `notes` field for how
these map to the demasking edge cases they test.

Regenerate CSV exports from the JSON source of truth:
```bash
python3 scripts/export_csv.py
```
