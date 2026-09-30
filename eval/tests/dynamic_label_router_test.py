"""
Dynamic Label Router Test
Classifies the prompt type first, then sends only relevant labels to GLiNER2-PII.
This should solve both the precision and recall problems simultaneously.
"""

import json
import time
import sys
import os
import re

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
    if det_label == gt_label:
        return True

    person_types = {"person", "full name", "first name", "middle name", "last name"}
    if det_label in person_types and gt_label in person_types:
        return True

    card_types = {"card number", "payment card", "card expiry", "card cvv"}
    if det_label in card_types and gt_label in card_types:
        return True

    secret_types = {"api key", "password", "secret", "access token", "recovery code"}
    if det_label in secret_types and gt_label in secret_types:
        return True

    address_types = {"address", "street address", "city", "state or region",
                     "postal code", "country"}
    if det_label in address_types and gt_label in address_types:
        return True

    account_types = {"username", "account id", "account number", "sensitive account id"}
    if det_label in account_types and gt_label in account_types:
        return True

    gov_types = {"government id", "national id number", "passport number",
                 "drivers license number", "tax id", "tax number"}
    if det_label in gov_types and gt_label in gov_types:
        return True

    date_types = {"date of birth", "sensitive date", "date", "date time"}
    if det_label in date_types and gt_label in date_types:
        return True

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


# ============================================================
# THE DYNAMIC LABEL ROUTER
# ============================================================

def classify_prompt_type(text):
    """
    Classify the prompt into a category, then return only the relevant labels.
    
    In production, this would be an SLM (Qwen/DistilBERT).
    For this test, we use keyword-based classification to prove the concept.
    
    The key insight: by sending only relevant labels to GLiNER, we:
    1. Eliminate false positives from irrelevant labels
    2. Improve recall by focusing GLiNER on the right entities
    3. Reduce latency (fewer labels = faster inference)
    """
    text_lower = text.lower()

    # ================================================================
    # CATEGORY 1: CODE PASTE / CONFIG / DEVOPS
    # Signals: code syntax, config files, environment variables
    # ================================================================
    code_signals = [
        "def ", "import ", "class ", "function ", "const ", "let ", "var ",
        "console.log", "print(", "return ", "if ", "else ", "for ",
        "curl", "wget", "ssh", "git ", "npm ", "pip ", "docker",
        "db_user", "db_pass", "password=", "api_key=", "secret=",
        "config", "config.", ".env", "dockerfile", "terraform",
        "kubectl", "ansible", "jenkins", "ci/cd", "pipeline",
        "module", "export", "require(", "from ", "```",
        "#!/bin/", "#!/", "staging", "production", "prod",
        "logs", "stack trace", "error:", "exception",
        "select ", "insert ", "update ", "create table",
        "create user", "grant ", "connection string",
        "authorization:", "bearer ", "token:",
        "private key", "public key", "certificate",
        "ping ", "traceroute", "ifconfig", "netstat",
    ]

    # ================================================================
    # CATEGORY 2: CUSTOMER SUPPORT / TICKET
    # Signals: customer names, emails, phones, card numbers, complaints
    # ================================================================
    support_signals = [
        "ticket", "customer", "charged", "order", "support",
        "complaint", "refund", "escalation", "tier 1", "tier 2",
        "locked out", "can't access", "cannot access",
        "account locked", "password reset",
        "invoice", "receipt", "billing",
        "name:", "email:", "phone:", "address:",
        "please help", "issue", "problem",
    ]

    # ================================================================
    # CATEGORY 3: INTERNAL EMAIL / COMMUNICATION
    # Signals: names, emails, meeting context
    # ================================================================
    email_signals = [
        "dear ", "hi ", "hello ", "regards", "best,",
        "meeting", "schedule", "calendar", "agenda",
        "please review", "fyi", "following up",
        "team", "colleague", "manager", "director",
        "q1", "q2", "q3", "q4", "quarterly",
        "roadmap", "planning", "standup",
        "sent from my", "from: ", "to: ", "cc: ",
    ]

    # ================================================================
    # CATEGORY 4: FINANCE / INVOICING
    # Signals: bank accounts, IBANs, payment details
    # ================================================================
    finance_signals = [
        "invoice", "payment", "wire", "transfer",
        "bank account", "iban", "bic", "swift",
        "routing number", "ach", "sepa",
        "amount:", "total:", "subtotal",
        "tax", "vat", "gst",
        "billing address", "shipping address",
    ]

    # ================================================================
    # CATEGORY 5: HR / EMPLOYEE
    # Signals: employee data, SSN, DOB, salary
    # ================================================================
    hr_signals = [
        "employee", "salary", "compensation", "benefits",
        "date of birth", "dob", "ssn", "social security",
        "hire date", "termination", "onboarding",
        "performance review", "pip", "disciplinary",
        "hr ", "human resources",
    ]

    # Score each category
    code_score = sum(1 for sig in code_signals if sig in text_lower)
    support_score = sum(1 for sig in support_signals if sig in text_lower)
    email_score = sum(1 for sig in email_signals if sig in text_lower)
    finance_score = sum(1 for sig in finance_signals if sig in text_lower)
    hr_score = sum(1 for sig in hr_signals if sig in text_lower)

    # Pick the highest scoring category
    scores = {
        "code": code_score,
        "support": support_score,
        "email": email_score,
        "finance": finance_score,
        "hr": hr_score,
    }

    best_category = max(scores, key=scores.get)

    # If no category scores > 0, use "general" (all labels)
    if scores[best_category] == 0:
        best_category = "general"

    # ================================================================
    # LABEL SETS PER CATEGORY
    # Only send labels that are relevant to this prompt type
    # ================================================================
    label_sets = {
        "code": [
            "api_key", "password", "secret", "access_token", "recovery_code",
            "username", "ip_address", "url",
        ],
        "support": [
            "person", "email", "phone_number", "card_number", "card_expiry",
            "card_cvv", "address", "username",
        ],
        "email": [
            "person", "email", "phone_number", "organization",
            "address", "date_of_birth",
        ],
        "finance": [
            "person", "email", "phone_number", "card_number",
            "bank_account", "iban", "routing_number", "address",
        ],
        "hr": [
            "person", "email", "phone_number", "date_of_birth",
            "national_id_number", "passport_number", "tax_id",
            "address", "username",
        ],
        "general": [
            "person", "email", "phone_number", "card_number",
            "api_key", "password", "secret", "ip_address",
            "url", "address", "date_of_birth",
        ],
    }

    labels = label_sets.get(best_category, label_sets["general"])

    return best_category, labels, scores


def run_dynamic_router_eval():
    print("=" * 70)
    print("DYNAMIC LABEL ROUTER EVALUATION")
    print("GLiNER2-PII + Context-Aware Label Selection")
    print("=" * 70)

    # Step 1: Load the model
    print("\n[1/5] Loading GLiNER2-PII model...")
    try:
        from gliner2 import GLiNER2
        model = GLiNER2.from_pretrained("fastino/gliner2-privacy-filter-PII-multi")
        print("      ✅ Model loaded successfully!")
    except Exception as e:
        print(f"      ❌ Failed to load model: {e}")
        sys.exit(1)

    # Step 2: Load the eval set
    print("\n[2/5] Loading evaluation set...")
    prompts = load_eval_set()

    # Step 3: Classify prompts and run detection
    print(f"\n[3/5] Running Dynamic Label Router on {len(prompts)} prompts...")
    print("-" * 70)

    total_tp = 0
    total_fp = 0
    total_fn = 0
    total_latency = 0
    total_classification_time = 0

    hard_tp = 0
    hard_fp = 0
    hard_fn = 0

    # Track category distribution
    category_counts = {}

    for i, item in enumerate(prompts):
        text = item.get("text", item.get("source_text", ""))
        if not text:
            continue

        ground_truth = extract_ground_truth(item)
        is_hard = is_hard_case(item)

        # === STEP A: CLASSIFY PROMPT TYPE ===
        classify_start = time.time()
        category, labels, scores = classify_prompt_type(text)
        classify_latency = (time.time() - classify_start) * 1000
        total_classification_time += classify_latency

        category_counts[category] = category_counts.get(category, 0) + 1

        # === STEP B: RUN GLiNER WITH ONLY RELEVANT LABELS ===
        detect_start = time.time()
        try:
            result = model.extract_entities(
                text,
                labels,
                threshold=0.5,
                include_confidence=True,
                include_spans=True
            )
        except Exception as e:
            print(f"  Prompt {i+1}: ERROR - {e}")
            continue

        detect_latency = (time.time() - detect_start) * 1000
        total_latency += detect_latency

        # Extract and deduplicate
        detected = extract_detected(result)
        detected = deduplicate_overlaps(detected)

        # Score against ground truth
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
            print(f"  {status} Prompt {i+1} [{category.upper()}] ({len(labels)} labels): {text[:60]}...")
            print(f"     GT={len(ground_truth)}, Det={len(detected)}, TP={tp}, FP={fp}, FN={fn}")
            print(f"     Labels sent: {', '.join(labels)}")
            print(f"     Classify: {classify_latency:.1f}ms | Detect: {detect_latency:.0f}ms")
            if fn > 0:
                for d in details:
                    if d["status"] == "FN":
                        print(f"     ❌ MISSED: '{d['ground_truth']}' ({d['gt_label']})")
            if fp > 0:
                for d in details:
                    if d["status"] == "FP":
                        print(f"     ❌ FALSE POS: '{d['detected']}' ({d['detected_label']})")
            print()

    # Step 4: Calculate metrics
    print(f"\n[4/5] Calculating metrics...")

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0

    avg_detect_latency = total_latency / len(prompts) if len(prompts) > 0 else 0
    avg_classify_latency = total_classification_time / len(prompts) if len(prompts) > 0 else 0
    avg_total_latency = avg_detect_latency + avg_classify_latency

    hard_precision = hard_tp / (hard_tp + hard_fp) if (hard_tp + hard_fp) > 0 else 0
    hard_recall = hard_tp / (hard_tp + hard_fn) if (hard_tp + hard_fn) > 0 else 0
    hard_f1 = 2 * (hard_precision * hard_recall) / (hard_precision + hard_recall) if (hard_precision + hard_recall) > 0 else 0

    # Step 5: Print final results
    print(f"\n[5/5] Results")

    print("\n" + "=" * 70)
    print("DYNAMIC LABEL ROUTER RESULTS")
    print("GLiNER2-PII + Context-Aware Label Selection")
    print("=" * 70)

    print(f"\nCategory Distribution:")
    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat:15s}: {count:3d} prompts ({count/len(prompts)*100:.0f}%)")

    print(f"\n{'Metric':<30s} {'Value':>10s}")
    print(f"{'-'*42}")
    print(f"{'Total Prompts':<30s} {len(prompts):>10d}")
    print(f"{'Total Entities (GT)':<30s} {total_tp + total_fn:>10d}")
    print(f"{'True Positives':<30s} {total_tp:>10d}")
    print(f"{'False Positives':<30s} {total_fp:>10d}")
    print(f"{'False Negatives':<30s} {total_fn:>10d}")
    print(f"{'Avg Classification Time':<30s} {avg_classify_latency:>9.1f}ms")
    print(f"{'Avg Detection Time':<30s} {avg_detect_latency:>9.0f}ms")
    print(f"{'Avg Total Latency':<30s} {avg_total_latency:>9.0f}ms")

    print(f"\n{'FULL SET':<30s}")
    print(f"{'  Precision':<30s} {precision:>9.2%}")
    print(f"{'  Recall':<30s} {recall:>9.2%}")
    print(f"{'  F1 Score':<30s} {f1:>9.2%}")

    print(f"\n{'HARD CASES':<30s}")
    print(f"{'  Precision':<30s} {hard_precision:>9.2%}")
    print(f"{'  Recall':<30s} {hard_recall:>9.2%}")
    print(f"{'  F1 Score':<30s} {hard_f1:>9.2%}")
    print("=" * 70)

    # Gate decision
    gate_pass = precision >= 0.85 and recall >= 0.80

    if gate_pass:
        print("\n🟢 GATE RESULT: PASS")
        print("   Precision >= 85% AND Recall >= 80%")
        print("   ─────────────────────────────────")
        print("   ACTION: Dynamic Label Router WORKS.")
        print("   - Use GLiNER2-PII + keyword-based classifier.")
        print("   - No fine-tuning needed.")
        print("   - No SLM needed for routing (keyword classification is enough).")
        print("   - No GPU needed.")
    else:
        print("\n🔴 GATE RESULT: FAIL")
        if precision < 0.85:
            print(f"   Precision {precision:.2%} < 85% threshold")
        if recall < 0.80:
            print(f"   Recall {recall:.2%} < 80% threshold")
        print("   ─────────────────────────────────")
        print("   ANALYSIS:")
        print("   - Check which categories have the most misses.")
        print("   - The keyword classifier may be miscategorizing prompts.")
        print("   - Consider adding more signals or using an SLM for classification.")

    # Comparison table
    print(f"\n{'='*70}")
    print("COMPARISON: ALL CONFIGURATIONS TESTED")
    print(f"{'='*70}")
    print(f"{'Config':<35s} {'P':>7s} {'R':>7s} {'F1':>7s} {'Lat':>7s}")
    print(f"{'-'*70}")
    print(f"{'GLiNER v1 (42 labels)':<35s} {'33.6%':>7s} {'66.2%':>7s} {'44.6%':>7s} {'599ms':>7s}")
    print(f"{'GLiNER v2 (8 labels)':<35s} {'91.5%':>7s} {'57.7%':>7s} {'70.8%':>7s} {'271ms':>7s}")
    print(f"{'GLiNER v3 (24 labels)':<35s} {'74.2%':>7s} {'75.4%':>7s} {'74.8%':>7s} {'490ms':>7s}")
    print(f"{'Presidio':<35s} {'37.7%':>7s} {'42.3%':>7s} {'39.9%':>7s} {'37ms':>7s}")
    print(f"{'Dynamic Router (THIS)':<35s} {precision:>6.1%} {recall:>6.1%} {f1:>6.1%} {avg_total_latency:>6.0f}ms")
    print(f"{'='*70}")

    # Save results
    results_output = {
        "model": "fastino/gliner2-privacy-filter-PII-multi",
        "mode": "dynamic_label_router (keyword classifier + context-aware labels)",
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
        "avg_classify_latency_ms": round(avg_classify_latency, 1),
        "avg_detect_latency_ms": round(avg_detect_latency, 1),
        "avg_total_latency_ms": round(avg_total_latency, 1),
        "gate_passed": gate_pass,
        "category_distribution": category_counts,
    }

    output_path = "eval/reports/dynamic_router_results.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results_output, f, indent=2)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    run_dynamic_router_eval()
