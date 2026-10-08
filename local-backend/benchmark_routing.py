"""Benchmark for the deterministic routing layer in dlp_core.

Measures:
- Total execution time
- Average latency per routing operation (microseconds)
- P50, P90, P95, P99 latency distribution
- Throughput (operations per second)

Workloads tested:
1. 10 entities (typical single-sentence prompt)
2. 100 entities (typical multi-paragraph document)
3. 1,000 entities (large document or log dump)
4. 10,000 entities (batch processing / stress test)

Usage:
    python benchmark_routing.py
    python -m pytest dlp_core/test_routing_benchmark.py
"""
from __future__ import annotations

import random
import time
from typing import Mapping

from dlp_core.policy import route_entity, route_label

# Representative entity types mixing all categories and actions:
# MASK (PII), REDACT+NO_STORE (credentials/PCI), REDACT+STORE (banking/internal),
# KEEP (URL), IP value-dependent (both global and internal)
BENCHMARK_CORPUS = [
    {"type": "PERSON", "text": "John Doe"},
    {"type": "EMAIL", "text": "john.doe@example.com"},
    {"type": "PHONE_NUMBER", "text": "+1-555-0199"},
    {"type": "ORGANIZATION", "text": "Acme Global Industries Corp"},
    {"type": "LOCATION", "text": "San Francisco, CA"},
    {"type": "API_KEY", "text": "sk-live-abcdef1234567890abcdef"},
    {"type": "PASSWORD", "text": "SuperSecretPass123!"},
    {"type": "JWT", "text": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDcSemACt8x4iTMC6Y5nVFU"},
    {"type": "CREDIT_CARD", "text": "4111111111111111"},
    {"type": "CVV", "text": "123"},
    {"type": "US_SSN", "text": "123-45-6789"},
    {"type": "IBAN", "text": "DE89370400440532013000"},
    {"type": "SWIFT_BIC", "text": "DEUTDEDDFXX"},
    {"type": "INTERNAL_URL", "text": "https://vault.internal.corp/secret"},
    {"type": "INTERNAL_HOSTNAME", "text": "prod-db-master.corp.local"},
    {"type": "URL", "text": "https://www.google.com/search?q=docs"},
    {"type": "IP_ADDRESS", "text": "8.8.8.8"},          # public IP -> KEEP
    {"type": "IP_ADDRESS", "text": "192.168.1.1"},      # private IP -> REDACT
    {"type": "IP_ADDRESS", "text": "10.0.0.50"},        # private IP -> REDACT
    {"type": "UNKNOWN_CUSTOM_TYPE", "text": "random"},  # unknown fail-safe
]


def generate_workload(count: int, seed: int = 42) -> list[Mapping[str, str]]:
    """Generate a reproducible list of N entities sampled from the corpus."""
    rng = random.Random(seed)
    return [rng.choice(BENCHMARK_CORPUS) for _ in range(count)]


def benchmark_workload(entities: list[Mapping[str, str]], iterations: int = 5) -> dict:
    """Benchmark routing for a workload of entities across multiple iterations."""
    latencies_us: list[float] = []

    # Warmup
    for e in entities[:10]:
        route_entity(e)

    # Measurement
    start_total = time.perf_counter()
    for _ in range(iterations):
        for e in entities:
            t0 = time.perf_counter()
            route_entity(e)
            t1 = time.perf_counter()
            latencies_us.append((t1 - t0) * 1_000_000.0)
    total_time_s = time.perf_counter() - start_total

    latencies_us.sort()
    total_ops = len(latencies_us)
    p50 = latencies_us[int(total_ops * 0.50)]
    p90 = latencies_us[int(total_ops * 0.90)]
    p95 = latencies_us[int(total_ops * 0.95)]
    p99 = latencies_us[int(total_ops * 0.99)]
    avg = sum(latencies_us) / total_ops
    ops_per_sec = total_ops / total_time_s

    return {
        "count": len(entities),
        "iterations": iterations,
        "total_ops": total_ops,
        "total_time_s": total_time_s,
        "avg_us": avg,
        "p50_us": p50,
        "p90_us": p90,
        "p95_us": p95,
        "p99_us": p99,
        "ops_per_sec": ops_per_sec,
    }


def run_all_benchmarks():
    workloads = [10, 100, 1_000, 10_000]
    iterations_map = {10: 100, 100: 50, 1_000: 10, 10_000: 3}

    print("==========================================================================================")
    print("              ROLE 4: DETERMINISTIC ROUTING PERFORMANCE BENCHMARK                         ")
    print("==========================================================================================")
    print(f"{'Entities':<10} | {'Total Ops':<10} | {'Total Time':<12} | {'Avg Latency':<12} | {'P95 Latency':<12} | {'Throughput':<16}")
    print("------------------------------------------------------------------------------------------")

    results = []
    for count in workloads:
        dataset = generate_workload(count)
        res = benchmark_workload(dataset, iterations=iterations_map[count])
        results.append(res)
        print(
            f"{count:<10} | "
            f"{res['total_ops']:<10} | "
            f"{res['total_time_s']*1000:8.2f} ms   | "
            f"{res['avg_us']:8.2f} µs   | "
            f"{res['p95_us']:8.2f} µs   | "
            f"{res['ops_per_sec']:10,.0f} ops/s"
        )
    print("==========================================================================================")
    return results


if __name__ == "__main__":
    run_all_benchmarks()
