#!/usr/bin/env python3
"""Fine-tune the Tier 2 GLiNER2-PII model on train.jsonl (GLiNER2 training format).

    python eval/detection_ft/scripts/finetune_tier2.py --train finetune/train.jsonl --out models/tier2_ft_v1

Then score it with the SAME test you used for the baseline:
    python -m dlp_core.eval.run_baseline --set dlp_core/eval/holdout_v1.jsonl --set eval/semantic_test_v2.jsonl \
        --t2-add organization phone_number --t2-model models/tier2_ft_v1/final --dump eval/reports/ft_v1

Notes
  * Conservative defaults (low learning rate, 3 epochs): the base model is already PII-tuned and we only want to
    nudge it, not overwrite it. train.jsonl already mixes ALL labels, so strong labels are "replayed".
  * A small slice of train.jsonl is held back as a validation set for monitoring ONLY. Never validate on
    semantic_test_v2.jsonl or the hold-out: choosing settings by looking at the test set makes the test meaningless.
  * CPU training of a ~200M-parameter model is slow (hours). A free Colab/Kaggle GPU is much faster.
  * Written from the GLiNER2 training tutorial; config fields your installed version does not know are skipped
    with a printed notice instead of crashing.
"""
import argparse
import dataclasses
import json
import random
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="fastino/gliner2-privacy-filter-PII-multi")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=5e-6, help="encoder learning rate")
    ap.add_argument("--task-lr", type=float, default=5e-5, help="learning rate of the task heads")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--grad-accum", type=int, default=2)
    ap.add_argument("--val-frac", type=float, default=0.05)
    ap.add_argument("--lora", action="store_true", help="LoRA adapters instead of full fine-tuning")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    from gliner2 import GLiNER2
    try:
        from gliner2.training.trainer import ExtractorTrainer as Trainer
    except ImportError:                                   # older name, same class
        from gliner2.training.trainer import GLiNER2Trainer as Trainer
    from gliner2.training.trainer import TrainingConfig

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = [l for l in Path(a.train).read_text(encoding="utf-8").splitlines() if l.strip()]
    random.Random(a.seed).shuffle(rows)
    n_val = max(1, int(len(rows) * a.val_frac))
    (out / "val.jsonl").write_text("\n".join(rows[:n_val]) + "\n", encoding="utf-8")
    (out / "train_fit.jsonl").write_text("\n".join(rows[n_val:]) + "\n", encoding="utf-8")
    print(f"{len(rows) - n_val} training rows, {n_val} validation rows")

    wanted = dict(output_dir=str(out), experiment_name="tier2_pii_ft", num_epochs=a.epochs,
                  batch_size=a.batch, gradient_accumulation_steps=a.grad_accum,
                  encoder_lr=a.lr, task_lr=a.task_lr, use_lora=a.lora, seed=a.seed)
    if a.lora:
        wanted.update(lora_r=8, lora_alpha=16.0, lora_dropout=0.0, lora_target_modules=["encoder"],
                      save_adapter_only=True)
    known = {f.name for f in dataclasses.fields(TrainingConfig)} if dataclasses.is_dataclass(TrainingConfig) else set(wanted)
    skipped = sorted(set(wanted) - known)
    if skipped:
        print(f"note: this gliner2 version has no TrainingConfig field(s) {skipped}; skipped")
    cfg = TrainingConfig(**{k: v for k, v in wanted.items() if k in known})

    model = GLiNER2.from_pretrained(a.base)
    trainer = Trainer(model, cfg)
    train_path, val_path = str(out / "train_fit.jsonl"), str(out / "val.jsonl")
    try:
        trainer.train(train_data=train_path, eval_data=val_path)
    except TypeError:                                     # some versions call it val_data
        trainer.train(train_data=train_path, val_data=val_path)

    final = out / "final"
    try:
        model.save_pretrained(str(final))
        print(f"\nsaved model to {final}")
    except Exception as e:                                # noqa: BLE001
        print(f"\ncould not save via save_pretrained ({type(e).__name__}). Look inside {out} for the checkpoint "
              f"folder the trainer wrote (it contains config + weights) and pass that to --t2-model.")
    print("next: run the baseline with --t2-model pointing at the saved folder.")


if __name__ == "__main__":
    main()
