# Detection fine-tuning kit (Tier 2 GLiNER2 + scoring)

Everything needed to measure and fine-tune the detection layer, kept apart from the pipeline code.
Full write-up with every run and every label: `REPORT.md` in this folder.

## Result in one table

Same test each time (team hold-out + `data/semantic_test_v2.jsonl`), Tier 2 threshold 0.3.

| | Untouched model | v5 + Tier 1 patches |
|---|---|---|
| Combined leak recall | 0.971 | **0.997** |
| Spans fully missed | 14 | **1** |
| Harmless texts flagged (of 303) | 234 | **39** |
| Strict F1 | 0.725 | **0.924** |

The test data is synthetic and the Tier 1 rules were written after reading its misses, so treat the numbers as
optimistic until a real-prompt test is run (see `scripts/make_real_test.py`).

## Layout

| Path | What |
|---|---|
| `data/train_v5.jsonl` | 6,500 synthetic training rows (GLiNER2 format). Labels longer than 8 words are split into pieces. |
| `data/semantic_test_v2.jsonl` | 1,200 synthetic test cases in the repo eval format (id, text, tags, spans + tier) |
| `scripts/generate_gliner_ft_data.py` | Generator for both files (no real data, no real people) |
| `scripts/finetune_tier2.py`, `scripts/train_on_colab_v5.ipynb` | Fine-tuning (script, or the Colab notebook for a free GPU) |
| `scripts/compare_reports.py` | Before/after per label from two scorer runs |
| `scripts/probe_tier2.py` | Try any sentence on one or more models |
| `scripts/make_real_test.py` | Turn hand-marked real prompts into a test file |
| `../../local-backend/dlp_core/eval/run_baseline.py` | The scorer: Tier 1, Tier 2 and combined, per label |

## How to run (from `local-backend/`)

```
# score the untouched Tier 2 model (+ organization and phone_number labels)
python -m dlp_core.eval.run_baseline --set dlp_core/eval/holdout_v1.jsonl --set ../eval/detection_ft/data/semantic_test_v2.jsonl --t2-add organization phone_number --dump ../eval/detection_ft/reports/base

# score a fine-tuned model (folder with config.json + model.safetensors)
python -m dlp_core.eval.run_baseline --set dlp_core/eval/holdout_v1.jsonl --set ../eval/detection_ft/data/semantic_test_v2.jsonl --t2-add organization phone_number --t2-model <model folder> --dump ../eval/detection_ft/reports/ft

# before / after
python ../eval/detection_ft/scripts/compare_reports.py ../eval/detection_ft/reports/base ../eval/detection_ft/reports/ft

# Tier 1 only (seconds, no model)
python -m dlp_core.eval.run_baseline --set dlp_core/eval/holdout_v1.jsonl --set ../eval/detection_ft/data/semantic_test_v2.jsonl --tier1-only
```

Adjust the hold-out path if it lives elsewhere. The scorer reuses `dlp_core/eval/metrics.py`; the hold-out file is never modified.

## The fine-tuned model is not in git

About 1.2 GB (GitHub's limit is 100 MB, and models are fetched, not committed). Stored at: https://huggingface.co/Hager290/DoppelFT (if the repo is private, run `hf auth login` first).
To use it, point `Tier2Config.model_name` at the folder. Whether to add `organization` and `phone_number` to
`DEFAULT_LABELS` is a team decision (see the report, Section 14).

## Rules of thumb learned

* Change one thing per training run. Stronger learning rates (v3) forgot dates and addresses.
* GLiNER2 marks spans of at most 8 words and reads `user@host.tld` as one word; deterministic Tier 1 rules cover those cases.
* Never train on the hold-out or on a real-prompt test file.

## Hold-out
The hold-out file is not in git. From local-backend/ run: python -m dlp_core.eval.make_holdout (it writes eval/holdout_v1.jsonl). Compare its sha256 with the team's frozen value before comparing results.
