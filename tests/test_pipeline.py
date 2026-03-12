"""
tests/test_pipeline.py — Integration tests for the rule-based ingest pipeline

Tests the full path: demo_works → work_to_claims → PatchChain → verify_integrity
without any external API calls.

Run:
    python -m pytest tests/
"""

import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib.util
import pytest

from alexandria_core.patch  import PatchChain, PatchEmitter
from alexandria_core.schema import ClaimNode, Category

# Load openalex_ingest for demo helpers
_spec = importlib.util.spec_from_file_location(
    "openalex_ingest",
    Path(__file__).resolve().parents[1] / "openalex_ingest.py",
)
_oi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_oi)


class TestDemoWorksToChain:
    def test_demo_works_returns_expected_count(self):
        works = _oi.demo_works("test query", 5)
        assert len(works) == 5

    def test_work_to_claims_produces_claims(self):
        works = _oi.demo_works("test", 1)
        claims = _oi.work_to_claims(works[0])
        assert len(claims) >= 1
        assert all(isinstance(c, ClaimNode) for c in claims)

    def test_claims_have_source_refs(self):
        works = _oi.demo_works("test", 2)
        for work in works:
            for claim in _oi.work_to_claims(work):
                assert len(claim.source_refs) >= 1

    def test_claims_have_non_empty_assumptions(self):
        works = _oi.demo_works("test", 2)
        for work in works:
            for claim in _oi.work_to_claims(work):
                assert len(claim.assumptions) >= 1

    def test_full_pipeline_integrity(self):
        works  = _oi.demo_works("mRNA", 5)
        claims = []
        for work in works:
            claims.extend(_oi.work_to_claims(work))

        chain   = PatchChain()
        emitter = PatchEmitter(chain)
        for c in claims:
            time.sleep(0.001)
            emitter.add(c)

        ok, violations = chain.verify_integrity()
        assert ok is True
        assert violations == []

    def test_all_categories_are_valid(self):
        works  = _oi.demo_works("test", 3)
        claims = []
        for work in works:
            claims.extend(_oi.work_to_claims(work))

        valid_cats = {cat.value for cat in Category}
        for c in claims:
            assert c.category.value in valid_cats
