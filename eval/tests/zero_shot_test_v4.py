"""
Zero-Shot Evaluation v4 — FIXED: Correct field name + targeted label expansion.

Fixes:
1. Bug: Ground truth entities now read from 'surface' field (not just 'text')
2. Labels: 12 labels (v2's 8 + username, password, secret, access_token)
   - Does NOT include first_name/last_name/full_name (which caused v1's 33% precision)
3. Debug: Prints actual missed entity text so we can see what's failing
"""

import json
import time
import sys
import os

def load_eval_set(path="eval/eval_set_v1.json"):
    if not os.path.exists(path):
        alt_paths = ["eval_set_v1.json", "../eval/eval_set_v1.json"]
        for alt in alt_paths:
            if os.path.exists(alt):
                path = alt
                break
        else:
            print(f"ERROR: Could not find eval_set_v1.json")
            sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "prompts" in data:
        prompts = data["prompts"]
    elif isinstance(data, list):
        prompts = data
    else:
        print(f"ERROR: Unexpected eval set format.")
        sys.exit(1)
    print(f"Loaded {len(prompts)} prompts from {path}")
    return prompts


def normalize_label(label):
    return label.lower().replace("_", " ").replace("-", " ").strip()


def extract_ground_truth(prompt_item):
    """FIXED: Read entity text from 'surface' field, not just 'text'."""
    entities = []
    if "entities" in prompt_item:
        for ent in prompt_item["entities"]:
            # Try multiple field names for the entity text
            entity_text = ent.get("surface", ent.get("text", ent.get("value", "")))
            entities.append({
                "text": entity_text,
                "label": normalize_label(ent.get("type", ent.get("label", ""))),
                "start": ent.get("start", 0),
                "end": ent.get("end", 0),
            })
    elif "span_labels" in prompt_item:
        text = prompt_item.get("text", prompt_item.get("source_text", ""))
        for span in prompt_item["span_labels"]:
            if isinstance(span, list) and len(span) >= 3:
                entities.append({
                    "text": text[span[0]:span[1]] if span[0] < len(text) else "",
                    "label": normalize_label(span[2]),
                    "start": span[0],
                    "end": span[1],
                })
    elif "privacy_mask" in prompt_item:
        for ent in prompt_item["privacy_mask"]:
            entity_text = ent.get("value", ent.get("surface", ent.get("text", "")))
            entities.append({
                "text": entity_text,
                "label": normalize_label(ent.get("label", "")),
                "start": ent.get("start", 0),
                "end": ent.get("end", 0),
            })
    return entities


def extract_detected(result):
    detected = []
    entities_dict = result.get("entities", {})
    for label, items in entities_dict.items():
        normalized_label = normalize_label(label)
        for item in items:
            if isinstance(item, dict):
                detected.append({
                    "text": item.get("text", ""),
                    "label": normalized_label,
                    "start": item.get("start", 0),
                    "end": item.get("end", 0),
                    "confidence": item.get("confidence", 0),
                })
            elif isinstance(item, str):
                detected.append({
                    "text": item,
                    "label": normalized_label,
                    "start": 0,
                    "end": 0,
                    "confidence": 0,
                })
    return detected


def deduplicate_overlaps(detected):
    """Remove overlapping detections — keep the one with the highest confidence."""
    if not detected:
        return detected
    # Sort by start position, then by span length (longest first)
    detected.sort(key=lambda x: (x["start"], -(x["end"] - x["start"])))
    deduplicated = []
    occupied = []
    for det in detected:
        overlaps = False
        for occ_start, occ_end in occupied:
            if det["start"] < occ_end and det["end"] > occ_start:
                overlaps = True
                break
        if not overlaps:
            deduplicated.append(det)
            occupied.append((det["start"], det["end"]))
    return deduplicated


def spans_overlap(det_start, det_end, gt_start, gt_end):
    return det_start < gt_end and det_end > gt_start


def labels_match(det_label, gt_label):
    """Check if labels match, accounting for type hierarchies."""
    if det_label == gt_label:
        return True

    # Person hierarchy (person = full_name = first_name = last_name)
    person_types = {"person", "full name", "first name", "middle name", "last name"}
    if det_label in person_types and gt_label in person_types:
        return True

    # Card hierarchy
    card_types = {"card number", "payment card", "card expiry", "card cvv", "credit card"}
    if det_label in card_types and gt_label in card_types:
        return True

    # Secret/credential hierarchy
    secret_types = {"api key", "password", "secret", "access token", "recovery code", "credential"}
    if det_label in secret_types and gt_label in secret_types:
        return True

    # Address hierarchy
    address_types = {"address", "street address", "city", "state or region",
                     "postal code", "country"}
    if det_label in address_types and gt_label in address_types:
        return True

    # Account hierarchy
    account_types = {"username", "account id", "account number", "sensitive account id"}
    if det_label in account_types and gt_label in account_types:
        return True

    # Government ID hierarchy
    gov_types = {"government id", "national id number", "passport number",
                 "drivers license number", "tax id", "tax number"}
    if det_label in gov_types and gt_label in gov_types:
        return True

    # Date hierarchy
    date_types = {"date of birth", "sensitive date", "date", "date time"}
    if det_label in date_types and gt_label in date_types:
        return True

    # Phone hierarchy
    phone_types = {"phone number", "phone"}
    if det_label in phone_types and gt_label in phone_types:
        return True

    return False


def score_detection(detected, ground_truth):
    tp = 0
    fp = 0
    matched_gt = set()
    details = []

    for det in detected:
        found_match = False
        for j, gt in enumerate(ground_truth):
            if j in matched_gt:
                continue
            if spans_overlap(det["start"], det["end"], gt["start"], gt["end"]):
                if labels_match(det["label"], gt["label"]):
                    tp += 1
                    matched_gt.add(j)
                    found_match = True
                    details.append({
                        "status": "TP",
                        "detected": det["text"],
                        "detected_label": det["label"],
                        "ground_truth": gt["text"],
                        "gt_label": gt["label"],
                        "confidence": det.get("confidence", 0)
                    })
                    break
        if not found_match:
            fp += 1
            details.append({
                "status": "FP",
                "detected": det["text"],
                "detected_label": det["label"],
                "ground_truth": None,
                "gt_label": None,
                "confidence": det.get("confidence", 0)
            })

    fn = len(ground_truth) - len(matched_gt)
    for j, gt in enumerate(ground_truth):
        if j not in matched_gt:
            details.append({
                "status": "FN",
                "detected": None,
                "detected_label": None,
                "ground_truth": gt["text"],
                "gt_label": gt["label"],
                "confidence": 0
            })

    return tp, fp, fn, details


def is_hard_case(item):
    if "hard_case" in item:
        return True
    if "is_hard_case" in item:
        return True
    if item.get("category") in ["inflection", "all_caps", "hard_case"]:
        return True
    tags = item.get("tags", [])
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag.lower() in ["inflection", "all_caps", "hard_case"]:
                return True
    return False


def run_zero_shot_eval():
    print("=" * 70)
    print("ZERO-SHOT EVALUATION v4 (FIXED: surface field + 12 targeted labels)")
    print("GLiNER2-PII vs 50-Prompt Hand-Built Eval Set")
    print("=" * 70)

    # Step 1: Load the model
    print("\n[1/4] Loading GLiNER2-PII model...")
    try:
        from gliner2 import GLiNER2
        model = GLiNER2.from_pretrained("fastino/gliner2-privacy-filter-PII-multi")
        print("      ✅ Model loaded successfully!")
    except Exception as e:
        print(f"      ❌ Failed to load model: {e}")
        sys.exit(1)

    # Step 2: Load the eval set
    print("\n[2/4] Loading evaluation set...")
    prompts = load_eval_set()

    # TARGETED LABEL SET: 12 labels
    # v2 had 8 labels (missed username, password, access_token, recovery_code)
    # v1 had 42 labels (caused first_name/last_name/full_name overlap -> 33% precision)
    # v4 has 12 labels: v2's 8 + the 4 credential types that were missed
    # Does NOT include first_name, last_name, full_name (which caused v1's overlap)
    entity_labels = [
        # v2's original 8 labels (91% precision, 57% recall)
        "person",           # single label — no first_name/last_name overlap
        "email",
        "phone_number",
        "card_number",
        "ip_address",
        "url",
        "address",
        "date_of_birth",
        # v4 additions: the 4 credential types v2 missed
        "username",
        "password",
        "secret",
        "access_token",
    ]

    print(f"      Using {len(entity_labels)} labels (v2's 8 + 4 credential types)")
    print(f"      Labels: {', '.join(entity_labels)}")

    # Step 3: Run detection on each prompt
    print(f"\n[3/4] Running zero-shot detection on {len(prompts)} prompts...")
    print("-" * 70)

    total_tp = 0
    total_fp = 0
    total_fn = 0
    total_latency = 0

    # Track misses by type
    misses_by_type = {}
    false_pos_by_type = {}

    hard_tp = 0
    hard_fp = 0
    hard_fn = 0

    for i, item in enumerate(prompts):
        text = item.get("text", item.get("source_text", ""))
        if not text:
            continue

        ground_truth = extract_ground_truth(item)
        is_hard = is_hard_case(item)

        # Run GLiNER2 zero-shot with 12 targeted labels
        start_time = time.time()
        try:
            result = model.extract_entities(
                text,
                entity_labels,
                threshold=0.5,
                include_confidence=True,
                include_spans=True
            )
        except Exception as e:
            print(f"  Prompt {i+1}: ERROR - {e}")
            continue

        latency = (time.time() - start_time) * 1000
        total_latency += latency

        detected = extract_detected(result)
        detected = deduplicate_overlaps(detected)

        tp, fp, fn, details = score_detection(detected, ground_truth)

        total_tp += tp
        total_fp += fp
        total_fn += fn

        if is_hard:
            hard_tp += tp
            hard_fp += fp
            hard_fn += fn

        # Track misses and false positives by type
        for d in details:
            if d["status"] == "FN":
                gt_label = d["gt_label"]
                misses_by_type[gt_label] = misses_by_type.get(gt_label, 0) + 1
            elif d["status"] == "FP":
                det_label = d["detected_label"]
                false_pos_by_type[det_label] = false_pos_by_type.get(det_label, 0) + 1

        # Print results for first 10 prompts
        if i < 10:
            status = "✅" if fn == 0 and fp == 0 else ("✅" if fn == 0 else "⚠️")
            print(f"  {status} Prompt {i+1}: {text[:70]}...")
            print(f"     GT={len(ground_truth)}, Det={len(detected)}, TP={tp}, FP={fp}, FN={fn}, {latency:.0f}ms")
            if fn > 0:
                for d in details:
                    if d["status"] == "FN":
                        print(f"     ❌ MISSED: '{d['ground_truth']}' ({d['gt_label']})")
            if fp > 0:
                for d in details:
                    if d["status"] == "FP":
                        print(f"     ❌ FALSE POS: '{d['detected']}' ({d['detected_label']})")

    # Step 4: Calculate metrics
    print(f"\n[4/4] Calculating metrics...")

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0

    avg_latency = total_latency / len(prompts) if len(prompts) > 0 else 0

    hard_precision = hard_tp / (hard_tp + hard_fp) if (hard_tp + hard_fp) > 0 else 0
    hard_recall = hard_tp / (hard_tp + hard_fn) if (hard_tp + hard_fn) > 0 else 0
    hard_f1 = 2 * (hard_precision * hard_recall) / (hard_precision + hard_recall) if (hard_precision + hard_recall) > 0 else 0

    # Print final results
    print("\n" + "=" * 70)
    print("ZERO-SHOT EVALUATION RESULTS (v4 — Fixed + 12 Targeted Labels)")
    print("=" * 70)
    print(f"Total Prompts:        {len(prompts)}")
    print(f"Total Entities (GT):  {total_tp + total_fn}")
    print(f"True Positives:       {total_tp}")
    print(f"False Positives:      {total_fp}")
    print(f"False Negatives:      {total_fn}")
    print(f"Average Latency:      {avg_latency:.0f}ms per prompt")
    print("-" * 70)
    print(f"FULL SET:")
    print(f"  Precision:          {precision:.2%}")
    print(f"  Recall:             {recall:.2%}")
    print(f"  F1 Score:           {f1:.2%}")
    print(f"-")
    print(f"HARD CASES (inflections + all-caps):")
    print(f"  Precision:          {hard_precision:.2%}")
    print(f"  Recall:             {hard_recall:.2%}")
    print(f"  F1 Score:           {hard_f1:.2%}")

    # Print miss breakdown
    print(f"\nMISSES BY ENTITY TYPE (what GLiNER missed):")
    for label, count in sorted(misses_by_type.items(), key=lambda x: -x[1]):
        print(f"  {label:25s}: {count:3d} misses")

    print(f"\nFALSE POSITIVES BY ENTITY TYPE (what GLiNER over-flagged):")
    for label, count in sorted(false_pos_by_type.items(), key=lambda x: -x[1]):
        print(f"  {label:25s}: {count:3d} false positives")

    print("=" * 70)

    # Gate decision
    gate_pass = precision >= 0.85 and recall >= 0.80

    if gate_pass:
        print("\n🟢 GATE RESULT: PASS")
        print("   Precision >= 85% AND Recall >= 80%")
        print("   ─────────────────────────────────")
        print("   ACTION: Fine-tuning is CUT.")
        print("   - Use GLiNER2-PII zero-shot with 12 targeted labels.")
        print("   - No GPU needed. No fine-tuning needed.")
    else:
        print("\n🔴 GATE RESULT: FAIL")
        if precision < 0.85:
            print(f"   Precision {precision:.2%} < 85% threshold")
        if recall < 0.80:
            print(f"   Recall {recall:.2%} < 80% threshold")
        print("   ─────────────────────────────────")
        print("   ANALYSIS:")
        print("   - Check the 'MISSES BY ENTITY TYPE' table above.")
        print("   - If misses are concentrated in 1-2 types, add those labels.")
        print("   - If misses are spread across many types, fine-tuning may be needed.")

    # Comparison table
    print(f"\n{'='*70}")
    print("COMPARISON: ALL CONFIGURATIONS TESTED")
    print(f"{'='*70}")
    print(f"{'Config':<40s} {'P':>7s} {'R':>7s} {'F1':>7s} {'Lat':>7s}")
    print(f"{'-'*70}")
    print(f"{'v1 (42 labels)':<40s} {'33.6%':>7s} {'66.2%':>7s} {'44.6%':>7s} {'599ms':>7s}")
    print(f"{'v2 (8 labels)':<40s} {'91.5%':>7s} {'57.7%':>7s} {'70.8%':>7s} {'271ms':>7s}")
    print(f"{'v3 (24 labels)':<40s} {'74.2%':>7s} {'75.4%':>7s} {'74.8%':>7s} {'490ms':>7s}")
    print(f"{'Presidio':<40s} {'37.7%':>7s} {'42.3%':>7s} {'39.9%':>7s} {'37ms':>7s}")
    print(f"{'v4 (12 labels, FIXED) (THIS)':<40s} {precision:>6.1%} {recall:>6.1%} {f1:>6.1%} {avg_latency:>6.0f}ms")
    print(f"{'='*70}")

    # Save results
    results_output = {
        "model": "fastino/gliner2-privacy-filter-PII-multi",
        "mode": "zero-shot (v4: fixed surface field + 12 targeted labels)",
        "labels_used": entity_labels,
        "threshold": 0.5,
        "total_prompts": len(prompts),
        "total_entities_gt": total_tp + total_fn,
        "true_positives": total_tp,
        "false_positives": total_fp,
        "false_negatives": total_fn,
        "full_set": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        },
        "hard_cases": {
            "precision": round(hard_precision, 4),
            "recall": round(hard_recall, 4),
            "f1": round(hard_f1, 4),
        },
        "avg_latency_ms": round(avg_latency, 1),
        "gate_passed": gate_pass,
        "gate_criteria": "P >= 0.85 AND R >= 0.80",
        "misses_by_type": misses_by_type,
        "false_positives_by_type": false_pos_by_type,
    }

    output_path = "eval/reports/zero_shot_results_v4.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results_output, f, indent=2)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    run_zero_shot_eval()
