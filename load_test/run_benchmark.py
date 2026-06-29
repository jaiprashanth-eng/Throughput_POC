#!/usr/bin/env python3
"""
Benchmark System A (MQ) vs System B (direct) price refresh.

Requires: requests (pip install requests)
Prerequisites: Django server, Redis, RabbitMQ, Celery worker for System A.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os

from dotenv import load_dotenv

load_dotenv()
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("Missing dependency: requests. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

POC_DIR = Path(__file__).resolve().parent.parent
RESULTS_PATH = Path(__file__).resolve().parent / "results.json"
DEFAULT_SEED_COUNT = 1000
POLL_INTERVAL_S = 0.2
POLL_TIMEOUT_S = 180
DISPATCH_SPREAD_TARGET_MS = 50


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = (len(sorted_vals) - 1) * p / 100
    lower = int(idx)
    upper = min(lower + 1, len(sorted_vals) - 1)
    weight = idx - lower
    return sorted_vals[lower] * (1 - weight) + sorted_vals[upper] * weight


def _fmt_ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f}ms"


def _fmt_rate(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f}/s"


def seed_products(count: int) -> None:
    print(f"Seeding {count} products...")
    subprocess.run(
        [sys.executable, "manage.py", "seed_products", "--count", str(count)],
        cwd=POC_DIR,
        check=True,
    )


def check_health(base_url: str) -> None:
    resp = requests.get(f"{base_url}/api/health/", timeout=10)
    resp.raise_for_status()
    health = resp.json()
    if not health.get("db"):
        raise RuntimeError("Health check failed: database unavailable")
    if not health.get("redis"):
        raise RuntimeError("Health check failed: redis unavailable")
    print(f"Health OK (db={health['db']}, redis={health['redis']})")


RABBITMQ_API_URL = os.getenv(
    "RABBITMQ_API_URL", "http://localhost:15672"
)
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")
MQ_QUEUE_NAME = "price_refresh_mq"


def purge_mq_queue() -> None:
    """Purge leftover messages from price_refresh_mq before each System A round."""
    try:
        resp = requests.delete(
            f"{RABBITMQ_API_URL}/api/queues/%2F/{MQ_QUEUE_NAME}/contents",
            auth=(RABBITMQ_USER, RABBITMQ_PASS),
            timeout=5,
        )
        if resp.status_code in (204, 200):
            pass  # purged successfully, no need to print
        elif resp.status_code == 404:
            pass  # queue doesn't exist yet, nothing to purge
        else:
            print(f"  WARNING: Queue purge returned HTTP {resp.status_code}")
    except requests.RequestException as exc:
        print(f"  WARNING: Could not purge queue (RabbitMQ not reachable?): {exc}")


def check_postgres_mode(use_postgres: bool) -> None:
    if use_postgres:
        if not os.getenv("DATABASE_URL"):
            print(
                "ERROR: --use-postgres was set but DATABASE_URL is not set.\n"
                "Example: export DATABASE_URL=postgres://poc:poc@localhost:5433/poc_db",
                file=sys.stderr,
            )
            sys.exit(1)
        print("Using PostgreSQL — results are production-realistic")
    else:
        print(
            "WARNING: SQLite has serialized writes. "
            "System B wall times may be inflated by 30-50% vs production Postgres."
        )


def post_refresh(base_url: str, system: str, product_ids: list[int]) -> dict[str, Any]:
    body: dict[str, Any] = {"product_ids": product_ids}
    if system == "a":
        body["async"] = True

    resp = requests.post(
        f"{base_url}/api/{system}/refresh/",
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def poll_until_done(base_url: str, system: str, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        resp = requests.get(f"{base_url}/api/{system}/jobs/{job_id}/", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "done":
            return data
        time.sleep(POLL_INTERVAL_S)

    raise TimeoutError(
        f"Job {job_id} (system {system}) did not complete within {POLL_TIMEOUT_S}s"
    )


def check_job_completeness(job_data: dict[str, Any], job_id: str) -> None:
    total = int(job_data.get("total", 0))
    completed = int(job_data.get("completed", 0))
    failed_count = int(job_data.get("failed_count", 0))
    accounted = completed + failed_count
    if accounted < total:
        missing = total - accounted
        print(
            f"WARNING: Job {job_id} has {missing} items unaccounted for — "
            "System B may have lost tasks silently."
        )


def build_job_result(
    system: str,
    job_id: str,
    total: int,
    dispatch_ms: float,
    job_data: dict[str, Any],
    *,
    round_num: int | None = None,
    job_index: int | None = None,
) -> dict[str, Any]:
    wall_time_ms = float(job_data["wall_time_ms"])
    items_per_second = total / (wall_time_ms / 1000) if wall_time_ms > 0 else 0.0
    result: dict[str, Any] = {
        "system": system,
        "system_label": "System A" if system == "a" else "System B",
        "job_id": job_id,
        "total": total,
        "dispatch_ms": dispatch_ms,
        "wall_time_ms": wall_time_ms,
        "completed": job_data.get("completed"),
        "failed_count": job_data.get("failed_count"),
        "items_per_second": items_per_second,
    }
    if round_num is not None:
        result["round"] = round_num
    if job_index is not None:
        result["job_index"] = job_index
    return result


def run_round(
    base_url: str,
    system: str,
    round_num: int,
    product_ids: list[int],
) -> dict[str, Any]:
    label = "System A" if system == "a" else "System B"
    print(f"  Round {round_num} — {label} ({len(product_ids)} products)...", flush=True)

    if system == "a":
        purge_mq_queue()

    refresh_data = post_refresh(base_url, system, product_ids)
    job_id = refresh_data["job_id"]
    dispatch_ms = float(refresh_data["dispatch_ms"])
    total = int(refresh_data["total"])

    job_data = poll_until_done(base_url, system, job_id)
    check_job_completeness(job_data, job_id)

    result = build_job_result(system, job_id, total, dispatch_ms, job_data, round_num=round_num)
    print(
        f"    dispatch={dispatch_ms:.1f}ms  wall={result['wall_time_ms']:.0f}ms  "
        f"throughput={result['items_per_second']:.1f}/s",
        flush=True,
    )
    return result


def run_sequential(
    base_url: str,
    rounds: int,
    product_ids: list[int],
) -> list[dict[str, Any]]:
    print(
        f"Running {rounds} rounds per system "
        f"(batch_size={len(product_ids)}, alternating A/B)..."
    )

    all_results: list[dict[str, Any]] = []
    for round_num in range(1, rounds + 1):
        print(f"\nRound {round_num}/{rounds}:")
        all_results.append(run_round(base_url, "a", round_num, product_ids))
        all_results.append(run_round(base_url, "b", round_num, product_ids))
    return all_results


def _dispatch_one_job(
    base_url: str,
    system: str,
    job_index: int,
    product_ids: list[int],
) -> dict[str, Any]:
    sent_at = time.monotonic()
    refresh_data = post_refresh(base_url, system, product_ids)
    return {
        "job_index": job_index,
        "sent_at": sent_at,
        "job_id": refresh_data["job_id"],
        "dispatch_ms": float(refresh_data["dispatch_ms"]),
        "total": int(refresh_data["total"]),
    }


def run_concurrent_system(
    base_url: str,
    system: str,
    concurrent_jobs: int,
    product_ids: list[int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    label = "System A" if system == "a" else "System B"
    print(
        f"\n{label}: firing {concurrent_jobs} concurrent jobs "
        f"({len(product_ids)} products each)...",
        flush=True,
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrent_jobs) as executor:
        dispatch_futures = [
            executor.submit(_dispatch_one_job, base_url, system, i, product_ids)
            for i in range(concurrent_jobs)
        ]
        dispatches = [future.result() for future in dispatch_futures]

    sent_times = [d["sent_at"] for d in dispatches]
    spread_ms = (max(sent_times) - min(sent_times)) * 1000
    if spread_ms > DISPATCH_SPREAD_TARGET_MS:
        print(
            f"  WARNING: dispatches spread over {spread_ms:.1f}ms "
            f"(target: <{DISPATCH_SPREAD_TARGET_MS}ms)"
        )
    else:
        print(f"  All {concurrent_jobs} jobs dispatched within {spread_ms:.1f}ms")

    first_dispatch_at = min(sent_times)

    completed_jobs: list[tuple[dict[str, Any], float]] = []
    timed_out_jobs: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrent_jobs) as executor:
        poll_futures = {
            executor.submit(poll_until_done, base_url, system, d["job_id"]): d
            for d in dispatches
        }
        for future in concurrent.futures.as_completed(poll_futures):
            dispatch_info = poll_futures[future]
            try:
                job_data = future.result()
                completed_at = time.monotonic()
                completed_jobs.append((dispatch_info, job_data, completed_at))
            except TimeoutError:
                timed_out_jobs.append(dispatch_info["job_id"])
                print(
                    f"  WARNING: Job {dispatch_info['job_id']} (index {dispatch_info['job_index']}) "
                    f"timed out after {POLL_TIMEOUT_S}s — excluded from results.",
                    flush=True,
                )

    if timed_out_jobs:
        print(f"  {len(timed_out_jobs)} of {concurrent_jobs} jobs timed out for {label}.")

    if not completed_jobs:
        print(f"  ERROR: All jobs timed out for {label}. Cannot compute elapsed time.")
        return [], {"concurrent_jobs": concurrent_jobs,
                    "time_from_first_dispatch_to_last_complete_ms": None,
                    "dispatch_spread_ms": spread_ms}

    last_complete_at = max(item[2] for item in completed_jobs)
    total_elapsed_ms = (last_complete_at - first_dispatch_at) * 1000

    job_results: list[dict[str, Any]] = []
    for dispatch_info, job_data, _ in sorted(completed_jobs, key=lambda x: x[0]["job_index"]):
        job_id = dispatch_info["job_id"]
        check_job_completeness(job_data, job_id)
        result = build_job_result(
            system,
            job_id,
            dispatch_info["total"],
            dispatch_info["dispatch_ms"],
            job_data,
            job_index=dispatch_info["job_index"],
        )
        job_results.append(result)
        print(
            f"  job {dispatch_info['job_index'] + 1}: dispatch={result['dispatch_ms']:.1f}ms  "
            f"wall={result['wall_time_ms']:.0f}ms  "
            f"throughput={result['items_per_second']:.1f}/s",
            flush=True,
        )

    burst_summary = {
        "concurrent_jobs": concurrent_jobs,
        "time_from_first_dispatch_to_last_complete_ms": total_elapsed_ms,
        "dispatch_spread_ms": spread_ms,
    }
    print(f"  Total elapsed (first POST → last done): {total_elapsed_ms:.0f}ms", flush=True)
    return job_results, burst_summary


def run_concurrent(
    base_url: str,
    concurrent_jobs: int,
    product_ids: list[int],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    print(
        f"Concurrent mode: {concurrent_jobs} simultaneous jobs per system "
        f"(batch_size={len(product_ids)})..."
    )

    jobs_a, burst_a = run_concurrent_system(base_url, "a", concurrent_jobs, product_ids)
    jobs_b, burst_b = run_concurrent_system(base_url, "b", concurrent_jobs, product_ids)

    all_results = jobs_a + jobs_b
    burst_summaries = {"a": burst_a, "b": burst_b}
    return all_results, burst_summaries


def summarize(results: list[dict[str, Any]], system: str) -> dict[str, Any]:
    system_results = [r for r in results if r["system"] == system]
    if not system_results:
        return {
            "rounds": 0,
            "avg_dispatch_ms": None,
            "avg_wall_time_ms": None,
            "p95_wall_time_ms": None,
            "avg_items_per_second": None,
            "total_elapsed_ms": None,
        }

    dispatch_vals = [r["dispatch_ms"] for r in system_results]
    wall_vals = [r["wall_time_ms"] for r in system_results]
    rate_vals = [r["items_per_second"] for r in system_results]

    return {
        "rounds": len(system_results),
        "avg_dispatch_ms": sum(dispatch_vals) / len(dispatch_vals),
        "avg_wall_time_ms": sum(wall_vals) / len(wall_vals),
        "p95_wall_time_ms": _percentile(wall_vals, 95),
        "avg_items_per_second": sum(rate_vals) / len(rate_vals),
        "total_elapsed_ms": None,
    }


def summarize_concurrent(
    results: list[dict[str, Any]],
    system: str,
    burst_summary: dict[str, Any],
) -> dict[str, Any]:
    summary = summarize(results, system)
    summary["total_elapsed_ms"] = burst_summary["time_from_first_dispatch_to_last_complete_ms"]
    summary["dispatch_spread_ms"] = burst_summary["dispatch_spread_ms"]
    return summary


def print_comparison_table(
    summary_a: dict[str, Any],
    summary_b: dict[str, Any],
    *,
    concurrent: bool = False,
) -> None:
    if concurrent:
        header = (
            f"{'System':<10} | {'Jobs':^6} | {'Avg dispatch_ms':^15} | "
            f"{'Avg wall_time_ms':^16} | {'p95 wall_ms':^12} | "
            f"{'Avg items/s':^12} | {'Total elapsed':^14}"
        )
    else:
        header = (
            f"{'System':<10} | {'Rounds':^6} | {'Avg dispatch_ms':^15} | "
            f"{'Avg wall_time_ms':^16} | {'p95 wall_ms':^12} | {'Avg items/s':^12}"
        )
    sep = "-" * len(header)

    if concurrent:
        row_a = (
            f"{'System A':<10} | {summary_a['rounds']:^6} | "
            f"{_fmt_ms(summary_a['avg_dispatch_ms']):^15} | "
            f"{_fmt_ms(summary_a['avg_wall_time_ms']):^16} | "
            f"{_fmt_ms(summary_a['p95_wall_time_ms']):^12} | "
            f"{_fmt_rate(summary_a['avg_items_per_second']):^12} | "
            f"{_fmt_ms(summary_a['total_elapsed_ms']):^14}"
        )
        row_b = (
            f"{'System B':<10} | {summary_b['rounds']:^6} | "
            f"{_fmt_ms(summary_b['avg_dispatch_ms']):^15} | "
            f"{_fmt_ms(summary_b['avg_wall_time_ms']):^16} | "
            f"{_fmt_ms(summary_b['p95_wall_time_ms']):^12} | "
            f"{_fmt_rate(summary_b['avg_items_per_second']):^12} | "
            f"{_fmt_ms(summary_b['total_elapsed_ms']):^14}"
        )
    else:
        row_a = (
            f"{'System A':<10} | {summary_a['rounds']:^6} | "
            f"{_fmt_ms(summary_a['avg_dispatch_ms']):^15} | "
            f"{_fmt_ms(summary_a['avg_wall_time_ms']):^16} | "
            f"{_fmt_ms(summary_a['p95_wall_time_ms']):^12} | "
            f"{_fmt_rate(summary_a['avg_items_per_second']):^12}"
        )
        row_b = (
            f"{'System B':<10} | {summary_b['rounds']:^6} | "
            f"{_fmt_ms(summary_b['avg_dispatch_ms']):^15} | "
            f"{_fmt_ms(summary_b['avg_wall_time_ms']):^16} | "
            f"{_fmt_ms(summary_b['p95_wall_time_ms']):^12} | "
            f"{_fmt_rate(summary_b['avg_items_per_second']):^12}"
        )

    print()
    print(header)
    print(sep)
    print(row_a)
    print(row_b)
    print()


def print_env_reminder(workers: int, latency_ms: int) -> None:
    print()
    print("Ensure these env vars are set on Django + Celery worker before benchmarking:")
    print(f"  export DIRECT_WORKER_THREADS={workers}")
    print(f"  export CELERYD_CONCURRENCY={workers}")
    print(f"  export MOCK_API_LATENCY_MS={latency_ms}")
    print("Restart runserver and Celery worker after changing env vars.")
    print()


def load_results_file() -> dict[str, Any]:
    if RESULTS_PATH.exists():
        try:
            data = json.loads(RESULTS_PATH.read_text())
            if "runs" in data:
                return data
        except json.JSONDecodeError:
            pass
    return {"runs": []}


def append_run(run_entry: dict[str, Any]) -> None:
    data = load_results_file()
    data["runs"].append(run_entry)
    RESULTS_PATH.write_text(json.dumps(data, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark System A (MQ) vs System B (direct) price refresh."
    )
    parser.add_argument(
        "--mode",
        choices=["sequential", "concurrent"],
        default="sequential",
        help="Benchmark mode (default: sequential)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Number of product IDs per job (default: 200)",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=5,
        help="Number of rounds per system, sequential mode only (default: 5)",
    )
    parser.add_argument(
        "--concurrent-jobs",
        type=int,
        default=5,
        help="Jobs fired simultaneously per system, concurrent mode only (default: 5)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Worker count parity hint (DIRECT_WORKER_THREADS / CELERYD_CONCURRENCY)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8000",
        help="Base URL of the Django dev server",
    )
    parser.add_argument(
        "--latency-ms",
        type=int,
        default=80,
        help="MOCK_API_LATENCY_MS parity hint (default: 80)",
    )
    parser.add_argument(
        "--use-postgres",
        action="store_true",
        help="Require DATABASE_URL (PostgreSQL) for production-realistic results",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    seed_count = max(DEFAULT_SEED_COUNT, args.batch_size)
    product_ids = list(range(1, args.batch_size + 1))

    print_env_reminder(args.workers, args.latency_ms)
    check_postgres_mode(args.use_postgres)

    seed_products(seed_count)
    check_health(base_url)

    print()
    burst_summaries: dict[str, dict[str, Any]] | None = None

    if args.mode == "sequential":
        all_results = run_sequential(base_url, args.rounds, product_ids)
        summary_a = summarize(all_results, "a")
        summary_b = summarize(all_results, "b")
        print_comparison_table(summary_a, summary_b, concurrent=False)
    else:
        all_results, burst_summaries = run_concurrent(
            base_url, args.concurrent_jobs, product_ids
        )
        summary_a = summarize_concurrent(all_results, "a", burst_summaries["a"])
        summary_b = summarize_concurrent(all_results, "b", burst_summaries["b"])
        print_comparison_table(summary_a, summary_b, concurrent=True)

    run_entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "concurrent_jobs": args.concurrent_jobs if args.mode == "concurrent" else None,
        "use_postgres": args.use_postgres,
        "database_url_set": bool(os.getenv("DATABASE_URL")),
        "config": {
            "batch_size": args.batch_size,
            "rounds": args.rounds,
            "workers": args.workers,
            "base_url": base_url,
            "latency_ms": args.latency_ms,
            "seed_count": seed_count,
            "product_ids": product_ids,
        },
        "jobs": all_results,
        "summary": {
            "a": summary_a,
            "b": summary_b,
        },
    }
    if burst_summaries:
        run_entry["burst_summaries"] = burst_summaries

    append_run(run_entry)
    print(f"Results appended to {RESULTS_PATH} (run #{len(load_results_file()['runs'])})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (requests.RequestException, subprocess.CalledProcessError, RuntimeError, TimeoutError) as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
