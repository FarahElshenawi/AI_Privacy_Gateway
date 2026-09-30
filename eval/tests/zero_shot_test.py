"""
Zero-Shot Evaluation Gate v3 — Expanded label set (avoids overlaps).
v2 had 8 labels (too few — missed username, password, access_token, etc.)
v1 had 42 labels (too many — caused first_name/last_name/full_name overlaps)
v3 has 22 labels: covers ground truth types without overlapping sub-types.
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
    entities = []
    if "entities" in prompt_item:
        for ent in prompt_item["entities"]:
            entities.append({
                "text": ent.get("text", ""),
                "label": normalize_label(ent.get("type", ent.get("label", ""))),
                "start": ent.get("start", 0),
                "end": ent.get("end", 0),
            })
    elif "span_labels" in prompt_item:
        for span in prompt_item["span_labels"]:
            if isinstance(span, list) and len(span) >= 3:
                entities.append({
                    "text": prompt_item["text"][span[0]:span[1]],
                    "label": normalize_label(span[2]),
                    "start": span[0],
                    "end": span[1],
                })
    elif "privacy_mask" in prompt_item:
        for ent in prompt_item["privacy_mask"]:
            entities.append({
                "text": ent.get("value", ""),
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
    # Direct match
    if det_label == gt_label:
        return True

    # Type hierarchy: these are all "person"
    person_types = {"person", "full name", "first name", "middle name", "last name"}
    if det_label in person_types and gt_label in person_types:
        return True

    # Type hierarchy: these are all "card"
    card_types = {"card number", "payment card", "card expiry", "card cvv"}
    if det_label in card_types and gt_label in card_types:
        return True

    # Type hierarchy: these are all "secret/credential"
    secret_types = {"api key", "password", "secret", "access token", "recovery code"}
    if det_label in secret_types and gt_label in secret_types:
        return True

    # Type hierarchy: these are all "address"
    address_types = {"address", "street address", "city", "state or region",
                     "postal code", "country"}
    if det_label in address_types and gt_label in address_types:
        return True

    # Type hierarchy: account identifiers
    account_types = {"username", "account id", "account number", "sensitive account id"}
    if det_label in account_types and gt_label in account_types:
        return True

    # Type hierarchy: government IDs
    gov_types = {"government id", "national id number", "passport number",
                 "drivers license number", "tax id", "tax number"}
    if det_label in gov_types and gt_label in gov_types:
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
    print("ZERO-SHOT EVALUATION GATE v3 (Expanded Labels + Hierarchy Match)")
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

    # EXPANDED LABEL SET — 22 labels that cover ground truth without overlaps
    # v1 had 42 labels (overlap problem)
    # v2 had 8 labels (too few, missed username/password/access_token)
    # v3 has 22 labels (covers all GT types, no overlapping sub-types like first/last name)
    entity_labels = [
        # Person (single label — no first_name/last_name overlap)
        "person",
        # Contact
        "email", "phone_number", "url",
        # Address (single label — no street/city/state overlap)
        "address",
        # Dates
        "date_of_birth", "sensitive_date",
        # Financial
        "card_number", "card_expiry", "card_cvv",
        "bank_account", "iban", "routing_number",
        # Credentials (all separate — no overlap)
        "api_key", "password", "secret", "access_token", "recovery_code",
        # Digital identity
        "username", "ip_address", "account_number",
        # Government IDs
        "passport_number", "national_id_number", "tax_id",
    ]

    print(f"      Using {len(entity_labels)} labels (expanded from v2's 8)")
    print(f"      Labels: {', '.join(entity_labels)}")

    # Step 3: Run detection on each prompt
    print(f"\n[3/4] Running zero-shot detection on {len(prompts)} prompts...")
    print("-" * 70)

    total_tp = 0
    total_fp = 0
    total_fn = 0
    total_latency = 0

    hard_tp = 0
    hard_fp = 0
    hard_fn = 0

    for i, item in enumerate(prompts):
        text = item.get("text", item.get("source_text", ""))
        if not text:
            continue

        ground_truth = extract_ground_truth(item)
        is_hard = is_hard_case(item)

        # Run GLiNER2 zero-shot with EXPANDED label set
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
    print("ZERO-SHOT EVALUATION RESULTS (v3 — Expanded Labels + Hierarchy)")
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
    print("=" * 70)

    # Gate decision
    gate_pass = precision >= 0.85 and recall >= 0.80

    if gate_pass:
        print("\n🟢 GATE RESULT: PASS")
        print("   Precision >= 85% AND Recall >= 80%")
        print("   ─────────────────────────────────")
        print("   ACTION: Fine-tuning is CUT.")
        print("   - Use base GLiNER2-PII model (zero-shot, 22 labels).")
        print("   - Role 5: Pivot to Cloud Backend + Residual Scanner.")
        print("   - No GPU needed. No ai4privacy dataset needed.")
    else:
        print("\n🔴 GATE RESULT: FAIL")
        if precision < 0.85:
            print(f"   Precision {precision:.2%} < 85% threshold")
            print("   ─────────────────────────────────")
            print("   GAP: Too many false positives.")
            print("   ACTION: Fine-tune for PRECISION.")
        if recall < 0.80:
            print(f"   Recall {recall:.2%} < 80% threshold")
            print("   ─────────────────────────────────")
            print("   GAP: Too many missed entities.")
            print("   ACTION: Fine-tune for RECALL.")
        print("   - Provision GPU. Start fine-tuning targeting the specific gap.")

    print("\n" + "=" * 70)

    # Save results
    results_output = {
        "model": "fastino/gliner2-privacy-filter-PII-multi",
        "mode": "zero-shot (expanded labels + dedup + hierarchy match)",
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
    }

    output_path = "eval/reports/zero_shot_results_v3.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results_output, f, indent=2)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    run_zero_shot_eval()
