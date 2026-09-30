"""
Presidio Zero-Shot Evaluation Gate
Tests Microsoft Presidio (regex + spaCy NER) against the 50-prompt eval set.
Compares results directly against the GLiNER2-PII zero-shot results.
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


def labels_match(det_label, gt_label):
    """Check if labels match, accounting for type hierarchies."""
    if det_label == gt_label:
        return True
    
    # Person hierarchy
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
    address_types = {"address", "street address", "city", "state or region", "postal code", "country", "location"}
    if det_label in address_types and gt_label in address_types:
        return True
    
    # Account hierarchy
    account_types = {"username", "account id", "account number", "sensitive account id", "user name"}
    if det_label in account_types and gt_label in account_types:
        return True
    
    # Government ID hierarchy
    gov_types = {"government id", "national id number", "passport number", "drivers license number", "tax id", "tax number"}
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


def spans_overlap(det_start, det_end, gt_start, gt_end):
    return det_start < gt_end and det_end > gt_start


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


def add_custom_recognizers(analyzer):
    """Add custom regex recognizers for credentials that Presidio doesn't have by default."""
    from presidio_analyzer import Pattern, PatternRecognizer
    
    # API Key patterns
    openai_key = Pattern(
        name="openai_api_key",
        regex=r"sk-[a-zA-Z0-9]{20,}",
        score=0.95
    )
    github_key = Pattern(
        name="github_token",
        regex=r"ghp_[a-zA-Z0-9]{36}",
        score=0.95
    )
    aws_key = Pattern(
        name="aws_access_key",
        regex=r"AKIA[0-9A-Z]{16}",
        score=0.95
    )
    slack_key = Pattern(
        name="slack_token",
        regex=r"xoxb-[0-9]{11}-[0-9]{11}-[a-zA-Z0-9]{24}",
        score=0.95
    )
    
    api_key_recognizer = PatternRecognizer(
        supported_entity="API_KEY",
        patterns=[openai_key, github_key, aws_key, slack_key],
        supported_language="en"
    )
    analyzer.registry.add_recognizer(api_key_recognizer)
    
    # Password pattern (KEY=value format)
    password_pattern = Pattern(
        name="password_assignment",
        regex=r"(?i)(?:password|passwd|pwd)\s*[=:]\s*([^\s\n]+)",
        score=0.85
    )
    password_recognizer = PatternRecognizer(
        supported_entity="PASSWORD",
        patterns=[password_pattern],
        supported_language="en"
    )
    analyzer.registry.add_recognizer(password_recognizer)
    
    # Secret/token pattern
    secret_pattern = Pattern(
        name="secret_assignment",
        regex=r"(?i)(?:secret|token|api_key)\s*[=:]\s*([^\s\n]+)",
        score=0.80
    )
    secret_recognizer = PatternRecognizer(
        supported_entity="SECRET",
        patterns=[secret_pattern],
        supported_language="en"
    )
    analyzer.registry.add_recognizer(secret_recognizer)
    
    # JWT pattern
    jwt_pattern = Pattern(
        name="jwt_token",
        regex=r"eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+",
        score=0.90
    )
    jwt_recognizer = PatternRecognizer(
        supported_entity="ACCESS_TOKEN",
        patterns=[jwt_pattern],
        supported_language="en"
    )
    analyzer.registry.add_recognizer(jwt_recognizer)
    
    # Bearer token pattern
    bearer_pattern = Pattern(
        name="bearer_token",
        regex=r"Bearer\s+([a-zA-Z0-9_\-.]+)",
        score=0.85
    )
    bearer_recognizer = PatternRecognizer(
        supported_entity="ACCESS_TOKEN",
        patterns=[bearer_pattern],
        supported_language="en"
    )
    analyzer.registry.add_recognizer(bearer_recognizer)
    
    # PEM private key
    pem_pattern = Pattern(
        name="pem_private_key",
        regex=r"-----BEGIN\s+[A-Z\s]+PRIVATE\s+KEY-----",
        score=0.99
    )
    pem_recognizer = PatternRecognizer(
        supported_entity="PRIVATE_KEY",
        patterns=[pem_pattern],
        supported_language="en"
    )
    analyzer.registry.add_recognizer(pem_recognizer)


def run_presidio_eval():
    print("=" * 70)
    print("PRESIDIO ZERO-SHOT EVALUATION GATE")
    print("Microsoft Presidio (spaCy NER + Regex) vs 50-Prompt Eval Set")
    print("=" * 70)

    # Step 1: Load Presidio
    print("\n[1/4] Loading Presidio Analyzer...")
    try:
        from presidio_analyzer import AnalyzerEngine
        analyzer = AnalyzerEngine()
        
        # Add custom recognizers for credentials
        add_custom_recognizers(analyzer)
        
        # List available recognizers
        recognizers = analyzer.get_recognizers()
        print(f"      ✅ Presidio loaded with {len(recognizers)} recognizers!")
        
        # List available entity types
        entity_types = analyzer.get_supported_entities()
        print(f"      Entity types: {len(entity_types)}")
        
    except Exception as e:
        print(f"      ❌ Failed to load Presidio: {e}")
        sys.exit(1)

    # Step 2: Load the eval set
    print("\n[2/4] Loading evaluation set...")
    prompts = load_eval_set()

    # Define which Presidio entity types to use
    presidio_entities = [
        "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD",
        "US_SSN", "US_PASSPORT", "URL", "IP_ADDRESS",
        "DATE_TIME", "LOCATION", "ORGANIZATION",
        # Custom entities we added
        "API_KEY", "PASSWORD", "SECRET", "ACCESS_TOKEN", "PRIVATE_KEY"
    ]

    print(f"      Requesting {len(presidio_entities)} entity types")

    # Step 3: Run detection
    print(f"\n[3/4] Running Presidio detection on {len(prompts)} prompts...")
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

        # Run Presidio
        start_time = time.time()
        try:
            results = analyzer.analyze(
                text=text,
                entities=presidio_entities,
                language="en",
                score_threshold=0.5
            )
        except Exception as e:
            print(f"  Prompt {i+1}: ERROR - {e}")
            continue

        latency = (time.time() - start_time) * 1000
        total_latency += latency

        # Convert Presidio results to our format
        detected = []
        for r in results:
            detected.append({
                "text": text[r.start:r.end],
                "label": normalize_label(r.entity_type),
                "start": r.start,
                "end": r.end,
                "confidence": r.score
            })

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
    print("PRESIDIO EVALUATION RESULTS")
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
        print("   ACTION: Use Presidio as detection engine.")
        print("   - No fine-tuning needed.")
        print("   - No GPU needed.")
        print("   - Presidio is the SDD default — supervisor will be happy.")
    else:
        print("\n🔴 GATE RESULT: FAIL")
        if precision < 0.85:
            print(f"   Precision {precision:.2%} < 85% threshold")
        if recall < 0.80:
            print(f"   Recall {recall:.2%} < 80% threshold")
        print("   ─────────────────────────────────")
        print("   Note: Compare these numbers against GLiNER2-PII results.")
        print("   If Presidio is better, switch detection engine to Presidio.")

    print("\n" + "=" * 70)

    # Save results
    results_output = {
        "model": "microsoft/presidio (spaCy en_core_web_lg + custom regex)",
        "mode": "zero-shot",
        "entities_requested": presidio_entities,
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

    output_path = "eval/reports/presidio_results.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results_output, f, indent=2)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    run_presidio_eval()
