#!/usr/bin/env python3
"""
examples/demo_pipeline.py — Minimal Alexandria Protocol demonstration

Runs the full rule-based pipeline against synthetic demo data (no API key
or network connection required).

Usage:
    python examples/demo_pipeline.py

Expected output (example):
    Fetched   5 synthetic works
    Extracted 30 claims
    Chain:    30 patches · head a3f9…
    Integrity: OK

    Top categories:
      EMPIRICAL    10
      MODEL        15
      SPECULATIVE   5
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Make sure the repo root is on sys.path when running from any directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alexandria_core.patch  import PatchChain, PatchEmitter
from alexandria_core.schema import ClaimNode

# openalex_ingest provides the demo data generator and rule-based extractor.
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "openalex_ingest",
    Path(__file__).resolve().parents[1] / "openalex_ingest.py",
)
_oi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_oi)


def run_demo(query: str = "epistemic graphs", max_works: int = 5) -> None:
    # ── 1. Fetch synthetic demo works ────────────────────────────────────────
    works = _oi.demo_works(query, max_works)
    print(f"Fetched   {len(works)} synthetic works")

    # ── 2. Extract claims (rule-based, no LLM) ───────────────────────────────
    raw_claims: list[ClaimNode] = []
    for work in works:
        raw_claims.extend(_oi.work_to_claims(work))
    print(f"Extracted {len(raw_claims)} claims")

    # ── 3. Build patch chain ─────────────────────────────────────────────────
    chain   = PatchChain()
    emitter = PatchEmitter(chain)
    for claim in raw_claims:
        time.sleep(0.001)   # satisfy chain monotonic timestamp requirement
        emitter.add(claim)

    print(f"Chain:    {chain.length} patches · head {chain.head_hash[:8]}…")

    # ── 4. Verify integrity ───────────────────────────────────────────────────
    ok, violations = chain.verify_integrity()
    status = "OK" if ok else f"FAILED ({len(violations)} violation(s))"
    print(f"Integrity: {status}")

    # ── 5. Summary by category ───────────────────────────────────────────────
    cat_counts: dict[str, int] = {}
    for claim in raw_claims:
        cat_counts[claim.category.value] = cat_counts.get(claim.category.value, 0) + 1

    print("\nTop categories:")
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat:<14} {count}")


if __name__ == "__main__":
    run_demo()
