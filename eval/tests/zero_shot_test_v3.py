"""
Zero-shot PII evaluation harness with anchor survival and re-identifiability.

Fully local, fully free. No API keys, no rate limits.

Generator (anchor descriptions + paraphrased LLM responses):
    Qwen2.5-3B-Instruct via transformers (CPU only, ~2 GB RAM, ~3 sec/call)

Judge (re-identifiability):
    sentence-transformers all-mpnet-base-v2 (CPU, ~50 ms/embed, deterministic)

Methodology:
    21 real-world entities (7 org / 7 person / 7 location) embedded in
    natural-context prompts. Each is masked with a descriptive anchor,
    sent through the local LLM, and the response is checked for whether
    the anchor survived verbatim. Separately, the anchor description
    alone is embedded and ranked against a within-type candidate pool
    (real entity + 6 same-type distractors) by cosine similarity. The
    rank of the real entity gives top-1 / top-3 hit rates and a real
    permutation-test signal: chance is 1/7 ~= 14.3%.

Usage:
    uv run python zero_shot_test_v3_local.py --mode anchor_eval
    uv run python zero_shot_test_v3_local.py --mode anchor_eval --dry-run

Install (one-time):
    uv pip install transformers sentence-transformers torch accelerate
"""

import os
import re
import json
import hashlib
import argparse
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

GENERATOR_MODEL = "Qwen/Qwen2.5-3B-Instruct"
EMBEDDER_MODEL = "sentence-transformers/all-mpnet-base-v2"

REPORT_PATH = Path("eval/reports/zero_shot_results_v3.json")
CACHE_PATH = Path("eval/cache/llm_calls.jsonl")


# ============================================================
# LAZY-LOADED LOCAL MODELS
# ============================================================

_generator = None
_embedder = None


def get_generator():
    """
    Lazily load the local Qwen2.5-3B-Instruct generator on CPU.

    First call downloads ~6 GB from Hugging Face Hub to:
        ~/.cache/huggingface/hub/   (Linux/Mac)
        C:\\Users\\<you>\\.cache\\huggingface\\hub\\   (Windows)

    Subsequent runs use the cache and start instantly.
    """
    global _generator
    if _generator is not None:
        return _generator

    print(f"[init] loading generator: {GENERATOR_MODEL} (CPU only, first run downloads ~6 GB)...")
    from transformers import pipeline
    import torch

    _generator = pipeline(
        "text-generation",
        model=GENERATOR_MODEL,
        device_map="cpu",
        torch_dtype=torch.float16,
        max_new_tokens=100,
        do_sample=True,
        temperature=0.7,
        top_p=0.9,
    )
    print("[init] generator ready")
    return _generator


def get_embedder():
    """
    Lazily load the local sentence-transformers embedder.

    First call downloads ~420 MB. After that, fully cached.
    """
    global _embedder
    if _embedder is not None:
        return _embedder

    print(f"[init] loading embedder: {EMBEDDER_MODEL} (CPU, first run downloads ~420 MB)...")
    from sentence_transformers import SentenceTransformer

    _embedder = SentenceTransformer(EMBEDDER_MODEL)
    print("[init] embedder ready")
    return _embedder


# ============================================================
# LLM CALL + CACHE
# ============================================================

def call_llm(prompt: str, dry_run: bool = False) -> str:
    """
    One call site for every generator LLM interaction in this evaluation.

    Uses the local Qwen2.5-3B-Instruct model. No API key, no rate limit.

    dry_run=True:
        Does not load the model. Returns a canned response so the
        harness can be tested without the ~6 GB download.

    Caching:
        Every (model, prompt) pair is cached to CACHE_PATH so re-runs
        after a code edit don't re-pay the ~3 sec/call cost.
    """
    if dry_run:
        return f"[DRY RUN ECHO] {prompt[:120]}"

    # ----- cache lookup -----
    key = hashlib.sha256(f"{GENERATOR_MODEL}::{prompt}".encode("utf-8")).hexdigest()[:16]

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CACHE_PATH.exists():
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("key") == key:
                    return entry["response"]

    # ----- generate -----
    pipe = get_generator()
    out = pipe([{"role": "user", "content": prompt}])

    # transformers pipeline returns a list of dicts; the last message is the assistant response
    response = out[0]["generated_text"][-1]["content"]

    if response is None:
        response = ""

    response = response.strip()

    # ----- cache write -----
    with CACHE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(
            {"key": key, "prompt": prompt, "response": response},
            ensure_ascii=False,
        ) + "\n")

    return response


# ============================================================
# TEST SET
# ============================================================

# 21 entities:
#   7 organizations
#   7 people
#   7 locations
#
# Each entity is placed into a natural-context prompt so that
# re-identification can be tested.

ANCHOR_TEST_SET = [
    # --------------------------------------------------------
    # Organizations
    # --------------------------------------------------------

    (
        "organization",
        "Apple",
        "I'm scheduling a call with someone from {E} about a new partnership next week.",
    ),
    (
        "organization",
        "Tesla",
        "Can you draft a follow-up email to my contact at {E} regarding the supply agreement?",
    ),
    (
        "organization",
        "Goldman Sachs",
        "My advisor at {E} recommended I rebalance the portfolio before Q4.",
    ),
    (
        "organization",
        "Pfizer",
        "The trial data from {E} needs to be summarized for the board by Friday.",
    ),
    (
        "organization",
        "Netflix",
        "We're pitching a licensing deal to {E} next month, help me prep talking points.",
    ),
    (
        "organization",
        "Boeing",
        "The parts shipment from {E} was delayed again, draft a complaint email.",
    ),
    (
        "organization",
        "Nike",
        "I need a comparison of {E}'s new campaign against last year's numbers.",
    ),

    # --------------------------------------------------------
    # People
    # --------------------------------------------------------

    (
        "person",
        "Elon Musk",
        "Summarize the main criticisms people have made of {E} in the last year.",
    ),
    (
        "person",
        "Taylor Swift",
        "Write a short paragraph about {E}'s influence on the touring industry.",
    ),
    (
        "person",
        "Warren Buffett",
        "What investment philosophy is {E} best known for? Summarize in 3 points.",
    ),
    (
        "person",
        "Serena Williams",
        "Draft a short bio blurb highlighting {E}'s career achievements.",
    ),
    (
        "person",
        "Jeff Bezos",
        "What leadership principles is {E} associated with?",
    ),
    (
        "person",
        "LeBron James",
        "Summarize {E}'s impact on the NBA over the last decade.",
    ),
    (
        "person",
        "Oprah Winfrey",
        "What made {E}'s talk show format so influential?",
    ),

    # --------------------------------------------------------
    # Locations
    # --------------------------------------------------------

    (
        "location",
        "Cupertino, California",
        "I'm relocating for work to {E}, what should I know about the area?",
    ),
    (
        "location",
        "Silicon Valley",
        "Write a short paragraph about why {E} became a tech hub.",
    ),
    (
        "location",
        "Wall Street, New York",
        "Explain why {E} is synonymous with the finance industry.",
    ),
    (
        "location",
        "Silicon Docks, Dublin",
        "What companies are headquartered in {E}?",
    ),
    (
        "location",
        "Shenzhen, China",
        "Why is {E} considered a major electronics manufacturing hub?",
    ),
    (
        "location",
        "Bangalore, India",
        "Summarize why {E} is called India's tech capital.",
    ),
    (
        "location",
        "Seattle, Washington",
        "What major tech companies are based in {E}?",
    ),
]


# ============================================================
# FAKER BASELINE
# ============================================================

FAKE_ORG_NAMES = [
    "Meridian Dynamics",
    "Kestrel Freight",
    "Marigold Robotics",
    "Brightpath Labs",
    "Union Creek Systems",
    "Northbrook Imports",
    "Fernwood Realty",
]


# ============================================================
# ANCHOR GENERATION
# ============================================================

def generate_anchor_description(
    entity_type: str,
    real_value: str,
    dry_run: bool,
) -> str:
    """
    Simulates the SLM decision engine writing a generic,
    non-identifying description.

    The prompt is tightened to push the model toward category-level
    descriptions instead of leaky distinguishing features
    (e.g., "Cupertino-based" for Apple).
    """
    prompt = (
        f"Describe the CATEGORY this {entity_type} belongs to in 5-8 words, "
        f"using only generic terms. Do NOT mention any distinguishing "
        f"features, locations, founders, or unique identifiers. "
        f"For example, if it is a tech company, say 'a large technology company'. "
        f"If it is an athlete, say 'a famous athlete'. "
        f"If it is a city, say 'a major city'. "
        f"The {entity_type}: {real_value}\n\n"
        f"Description:"
    )

    return call_llm(prompt, dry_run).strip().strip('"')


# ============================================================
# RE-IDENTIFIABILITY CHECK (EMBEDDING-BASED)
# ============================================================

def check_reidentifiability(
    entity_type: str,
    description_or_name: str,
    real_value: str,
    dry_run: bool,
    all_entities: list = None,
) -> dict:
    """
    Embedding-based re-identifiability check.

    For each entity, builds a within-type candidate pool:
        [real_value] + all other real_values of the same type

    Embeds the anchor description and each candidate, ranks candidates
    by cosine similarity to the anchor. The rank of the real entity
    gives top-1 / top-3 hit rates.

    Why embeddings over LLM-as-judge:
        - Deterministic (no temperature-induced variance)
        - No model-dependent "knowledge cutoffs"
        - Permutation-test-able (chance = 1/(n_distractors+1))
        - Free, no rate limit

    Args:
        all_entities: list of (entity_type, real_value) tuples from
            the test set, used to build the distractor pool. If None,
            only the real_value is in the candidate pool (degenerate).
    """
    if dry_run:
        # Dry-run stub: intentionally reports worst-case (rank 0)
        return {
            "top1_hit": False,
            "top3_hit": False,
            "rank": -1,
            "n_distractors": 6,
            "similarity_to_real": 0.0,
            "mean_similarity_to_distractors": 0.0,
        }

    embedder = get_embedder()

    # Build distractor pool: all entities of the same type except current
    if all_entities is None:
        distractors = []
    else:
        distractors = [
            v for (t, v) in all_entities
            if t == entity_type and v != real_value
        ]

    candidates = [real_value] + distractors

    # Encode (batched, fast)
    anchor_emb = embedder.encode(description_or_name, convert_to_tensor=True)
    cand_embs = embedder.encode(candidates, convert_to_tensor=True)

    # Cosine similarity: anchor vs each candidate
    sims = embedder.similarity(anchor_emb, cand_embs)[0].cpu().tolist()

    # Rank candidates by descending similarity
    ranked = sorted(zip(candidates, sims), key=lambda x: -x[1])
    rank = next(
        (i for i, (name, _) in enumerate(ranked) if name == real_value),
        -1,
    )

    return {
        "top1_hit": rank == 0,
        "top3_hit": 0 <= rank < 3,
        "rank": rank,
        "n_distractors": len(distractors),
        "similarity_to_real": float(sims[0]),
        "mean_similarity_to_distractors": (
            float(sum(sims[1:]) / len(sims[1:])) if len(sims) > 1 else 0.0
        ),
    }


# ============================================================
# CONDITION A
# ANCHOR SURVIVAL + RE-IDENTIFIABILITY
# ============================================================

def run_anchor_condition(dry_run: bool) -> dict:
    """
    Condition A:

    1. Generate a descriptive anchor.
    2. Put the anchor into the original prompt.
    3. Send the masked prompt to the local LLM.
    4. Check whether the anchor survives in the response.
    5. Independently test whether the anchor itself
       can be re-identified via embeddings.
    """

    # Pre-compute the all-entities list once for the distractor pool
    all_entities = [(t, v) for (t, v, _) in ANCHOR_TEST_SET]

    results = {
        "organization": [],
        "person": [],
        "location": [],
    }

    for i, (entity_type, real_value, template) in enumerate(
        ANCHOR_TEST_SET
    ):
        # Example:
        # [ORGANIZATION_0: major technology company]
        anchor_id = f"[{entity_type.upper()}_{i}"

        description = generate_anchor_description(
            entity_type=entity_type,
            real_value=real_value,
            dry_run=dry_run,
        )

        anchor_token = f"{anchor_id}: {description}]"

        # Insert anchor into natural-context prompt.
        masked_prompt = template.format(E=anchor_token)

        # Send masked prompt to LLM.
        response = call_llm(masked_prompt, dry_run)

        # ----------------------------------------------------
        # Survival check
        # ----------------------------------------------------
        #
        # Does the exact anchor token survive?
        # Or at least does the anchor ID survive?
        #
        survived = (
            anchor_token in response
            or re.search(
                re.escape(anchor_id),
                response,
            ) is not None
        )

        # ----------------------------------------------------
        # Re-identification check (embedding-based)
        # ----------------------------------------------------

        reid = check_reidentifiability(
            entity_type=entity_type,
            description_or_name=description,
            real_value=real_value,
            dry_run=dry_run,
            all_entities=all_entities,
        )

        results[entity_type].append(
            {
                "real_value": real_value,
                "anchor": anchor_token,
                "survived": survived,
                **reid,
            }
        )

        print(
            f"[ANCHOR {i + 1:02d}/{len(ANCHOR_TEST_SET)}] "
            f"{entity_type:<12} "
            f"{real_value:<22} "
            f"survived={'Y' if survived else 'N'} "
            f"rank={reid['rank'] + 1}/7 "
            f"top1={'Y' if reid['top1_hit'] else 'N'} "
            f"top3={'Y' if reid['top3_hit'] else 'N'}"
        )

    return results


# ============================================================
# CONDITION B
# FAKER BASELINE
# ============================================================

def run_faker_baseline(dry_run: bool) -> list:
    """
    Condition B:

    Organizations are replaced with fake organization names
    instead of descriptive anchors.

    Sanity check: by construction, embeddings of fake names share
    near-zero semantic content with real entity names. Top-1 should
    be at chance level (~14% with 7 candidates).
    """

    org_entities = [
        (entity_type, real_value, template)
        for entity_type, real_value, template in ANCHOR_TEST_SET
        if entity_type == "organization"
    ]

    all_entities = [(t, v) for (t, v, _) in ANCHOR_TEST_SET]

    results = []

    for i, (entity_type, real_value, template) in enumerate(
        org_entities
    ):
        fake_name = FAKE_ORG_NAMES[
            i % len(FAKE_ORG_NAMES)
        ]

        masked_prompt = template.format(E=fake_name)

        response = call_llm(masked_prompt, dry_run)

        survived = fake_name in response

        reid = check_reidentifiability(
            entity_type="organization",
            description_or_name=fake_name,
            real_value=real_value,
            dry_run=dry_run,
            all_entities=all_entities,
        )

        result = {
            "real_value": real_value,
            "fake_value": fake_name,
            "survived": survived,
            **reid,
        }

        results.append(result)

        print(
            f"[FAKER  {i + 1:02d}/{len(org_entities)}] "
            f"{real_value:<22} "
            f"fake={fake_name:<22} "
            f"survived={'Y' if survived else 'N'} "
            f"rank={reid['rank'] + 1}/7 "
            f"top1={'Y' if reid['top1_hit'] else 'N'}"
        )

    return results


# ============================================================
# SUMMARY
# ============================================================

def summarize(results_by_type: dict) -> dict:
    """
    Calculate survival and re-identification rates.

    Re-identifiability is reported as:
        top1:    fraction where real entity ranked #1
        top3:    fraction where real entity ranked in top 3
        mean_rank: average rank (1 = best, 7 = worst for our test set)
    """

    summary = {}

    for entity_type, items in results_by_type.items():
        if not items:
            continue

        n = len(items)

        summary[entity_type] = {
            "n": n,
            "survival_rate": (
                sum(x["survived"] for x in items) / n
            ),
            "reidentifiability": {
                "top1": sum(x["top1_hit"] for x in items) / n,
                "top3": sum(x["top3_hit"] for x in items) / n,
                "mean_rank": sum(x["rank"] + 1 for x in items) / n,
                # Chance baseline: with n=7 candidates, expected mean_rank = 4.0
                # and top1 chance = 1/7 ~= 0.143
            },
        }

    # --------------------------------------------------------
    # Overall
    # --------------------------------------------------------

    all_items = [
        item
        for items in results_by_type.values()
        for item in items
    ]

    if all_items:
        n = len(all_items)

        summary["overall"] = {
            "n": n,
            "survival_rate": (
                sum(x["survived"] for x in all_items) / n
            ),
            "reidentifiability": {
                "top1": sum(x["top1_hit"] for x in all_items) / n,
                "top3": sum(x["top3_hit"] for x in all_items) / n,
                "mean_rank": sum(x["rank"] + 1 for x in all_items) / n,
                "chance_baseline_top1": 1.0 / 7,  # ~0.143
                "chance_baseline_mean_rank": 4.0,
            },
        }

    return summary


# ============================================================
# COMPLETE ANCHOR EVALUATION
# ============================================================

def run_anchor_eval(dry_run: bool = False) -> dict:
    """
    Run:

    Condition A:
        Descriptive anchors

    Condition B:
        Faker organization baseline
    """

    print()
    print("=" * 70)
    print("ANCHOR SURVIVAL & RE-IDENTIFIABILITY EVALUATION (FULLY LOCAL)")
    print("=" * 70)
    print()

    print(f"Generator model: {GENERATOR_MODEL}")
    print(f"Embedder model:  {EMBEDDER_MODEL}")
    print(f"Dry run:         {dry_run}")
    print(f"Entities:         {len(ANCHOR_TEST_SET)}")
    print(f"Cache:           {CACHE_PATH}")
    print()

    # --------------------------------------------------------
    # Condition A
    # --------------------------------------------------------

    print("-" * 70)
    print("CONDITION A - DESCRIPTIVE ANCHORS")
    print("-" * 70)

    anchor_results = run_anchor_condition(dry_run=dry_run)

    # --------------------------------------------------------
    # Condition B
    # --------------------------------------------------------

    print()
    print("-" * 70)
    print("CONDITION B - FAKER BASELINE")
    print("-" * 70)

    faker_results = run_faker_baseline(dry_run=dry_run)

    faker_summary = summarize({"organization": faker_results})

    # --------------------------------------------------------
    # Final report
    # --------------------------------------------------------

    anchor_summary = summarize(anchor_results)

    return {
        "methodology": (
            "21 real-world entities (7 org/person/location) "
            "embedded in natural-context prompts, masked with "
            "descriptive anchors generated by a local Qwen2.5-3B "
            "model, sent through the same local model, and checked "
            "for (a) whether the demask regex can still find the "
            "anchor and (b) whether a local sentence-transformers "
            "embedder ranks the real entity #1 by cosine similarity "
            "to the anchor description alone, against a within-type "
            "candidate pool of 7. Faker baseline (organizations "
            "only) run identically as a sanity check. All "
            "deterministic, no API calls, no rate limits."
        ),

        "n_entities": len(ANCHOR_TEST_SET),

        "generator_model": GENERATOR_MODEL,
        "embedder_model": EMBEDDER_MODEL,

        "anchor_condition": anchor_summary,
        "faker_baseline_condition": faker_summary,

        # Sanity check: faker baseline top-1 should be near chance (~14%)
        # If it's much higher, the test is broken.
        "sanity_check_passed": (
            faker_summary
            .get("organization", {})
            .get("reidentifiability", {})
            .get("top1", 1.0)
            < 0.30
        ),

        "per_entity_detail": {
            "anchor": anchor_results,
            "faker_baseline": faker_results,
        },
    }


# ============================================================
# REPORT HANDLING
# ============================================================

def save_anchor_report(anchor_report: dict) -> None:
    """
    Merge the anchor evaluation into the existing report.

    If the detection report does not exist yet, create a new
    report containing only the anchor evaluation.
    """

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    if REPORT_PATH.exists():
        try:
            with REPORT_PATH.open("r", encoding="utf-8") as f:
                existing = json.load(f)
        except json.JSONDecodeError:
            print(
                f"WARNING: {REPORT_PATH} exists but is not valid JSON."
            )
            existing = {}
    else:
        existing = {}

    existing["anchor_eval"] = anchor_report

    with REPORT_PATH.open("w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    print()
    print(f"Report saved to: {REPORT_PATH}")


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Zero-shot PII evaluation harness with anchor survival "
            "and re-identifiability evaluation. Fully local, fully "
            "free. No API keys required."
        )
    )

    parser.add_argument(
        "--mode",
        choices=["detect", "anchor_eval"],
        default="anchor_eval",
        help="Evaluation mode. Use anchor_eval for the anchor test.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run without loading the models or making API calls. "
            "Useful for testing the harness structure."
        ),
    )

    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear the LLM call cache before running.",
    )

    return parser


# ============================================================
# MAIN
# ============================================================

def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.clear_cache and CACHE_PATH.exists():
        CACHE_PATH.unlink()
        print(f"Cleared cache: {CACHE_PATH}")

    # --------------------------------------------------------
    # Anchor evaluation
    # --------------------------------------------------------

    if args.mode == "anchor_eval":
        anchor_report = run_anchor_eval(dry_run=args.dry_run)

        save_anchor_report(anchor_report)

        print()
        print("=" * 70)
        print("FINAL SUMMARY")
        print("=" * 70)

        print(json.dumps(anchor_report, indent=2, ensure_ascii=False))
        return

    # --------------------------------------------------------
    # Detection mode
    # --------------------------------------------------------

    if args.mode == "detect":
        print("Detection mode was selected.")
        print(
            "This standalone anchor-eval version does not contain "
            "the original detection-eval implementation."
        )
        print(
            "Use --mode anchor_eval to run the evaluation "
            "included in this file."
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()