"""
tests/test_patch.py — Unit tests for alexandria_core.patch (PatchChain, PatchEmitter)

Run:
    python -m pytest tests/
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import time
import pytest

from alexandria_core.patch  import PatchChain, PatchEmitter
from alexandria_core.schema import ClaimNode, Category, Modality


def _make_claim(**kwargs) -> ClaimNode:
    defaults = dict(
        subject="Subject",
        predicate="RELATES_TO",
        object="Object",
        category=Category.MODEL,
        modality=Modality.HYPOTHESIS,
        assumptions=["test assumption"],
        source_refs=["test:W0001"],
    )
    defaults.update(kwargs)
    return ClaimNode.new(**defaults)


class TestPatchChain:
    def test_empty_chain_has_zero_length(self):
        chain = PatchChain()
        assert chain.length == 0

    def test_chain_grows_after_emit(self):
        chain   = PatchChain()
        emitter = PatchEmitter(chain)
        emitter.add(_make_claim())
        assert chain.length == 1

    def test_integrity_holds_after_single_patch(self):
        chain   = PatchChain()
        emitter = PatchEmitter(chain)
        emitter.add(_make_claim())
        ok, violations = chain.verify_integrity()
        assert ok is True
        assert violations == []

    def test_integrity_holds_for_multiple_patches(self):
        chain   = PatchChain()
        emitter = PatchEmitter(chain)
        for i in range(5):
            time.sleep(0.002)
            emitter.add(_make_claim(subject=f"Subject_{i}"))
        ok, violations = chain.verify_integrity()
        assert ok is True

    def test_head_hash_changes_with_each_patch(self):
        chain   = PatchChain()
        emitter = PatchEmitter(chain)
        emitter.add(_make_claim())
        h1 = chain.head_hash
        time.sleep(0.002)
        emitter.add(_make_claim(subject="Different"))
        h2 = chain.head_hash
        assert h1 != h2

    def test_chain_length_matches_patch_count(self):
        chain   = PatchChain()
        emitter = PatchEmitter(chain)
        n = 3
        for i in range(n):
            time.sleep(0.002)
            emitter.add(_make_claim(subject=f"S{i}"))
        assert chain.length == n
