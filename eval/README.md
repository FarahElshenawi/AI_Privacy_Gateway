# Evaluation material

Three places hold evaluation work. Only the first is the live harness.

| Place | What it is |
|-------|-----------|
| `local-backend/dlp_core/eval/` | **The harness.** Metrics, `run.py`, the Tier 2 bake-off (`bakeoff.py`), the generators for the dev and frozen hold-out sets (`make_*.py`), and `report.json`. The hold-out file is regenerated with `python -m dlp_core.eval.make_holdout` and checked against `holdout_v1.sha256`. Tests: `test_eval.py`, `test_holdout_frozen.py`. |
| `local-backend/eval/` | An earlier 500-example set and its generator (`generate_eval_set.py`) with its false-positive/negative dumps. Kept for reference. |
| `eval/` (this folder) | **Week 1 research archive.** `eval_set_v1.json` (50 hand-built prompts, with CSV exports), zero-shot experiments in `tests/` (not run by CI), and notebooks in `Detection Model Experiments/`. The ADR `docs/adr/0001-zero-shot-before-finetuning.md` describes why. |

The bake-off command is in the root README (Evaluation section).
