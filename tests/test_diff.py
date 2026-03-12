"""
tests/test_diff.py — Unit tests for alexandria_core.diff (DiffEngine)

Run:
    python -m pytest tests/
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from alexandria_core.schema import ClaimNode, Category, Modality, BuilderOrigin
from alexandria_core.diff   import DiffEngine


def _claim(subject: str, predicate: str, obj: str,
           category: Category = Category.EMPIRICAL,
           modality: Modality  = Modality.EVIDENCE,
           origin: BuilderOrigin = BuilderOrigin.ALPHA) -> ClaimNode:
    return ClaimNode.new(
        subject=subject, predicate=predicate, object=obj,
        category=category, modality=modality,
        builder_origin=origin,
        assumptions=["test"],
        source_refs=["test:W0001"],
    )


class TestDiffEngine:
    def setup_method(self):
        self.engine = DiffEngine()

    def test_no_diffs_for_identical_graphs(self):
        c = _claim("A", "RELATES_TO", "B")
        report = self.engine.compare([c], [c])
        assert len(report.diffs) == 0

    def test_detects_missing_claim_in_beta(self):
        alpha_only = _claim("X", "CONTRIBUTES_TO", "Y")
        report = self.engine.compare([alpha_only], [])
        assert len(report.diffs) >= 1

    def test_detects_modality_conflict(self):
        c_alpha = _claim("A", "RELATES_TO", "B", modality=Modality.HYPOTHESIS)
        c_beta  = _claim("A", "RELATES_TO", "B", modality=Modality.ESTABLISHED,
                         origin=BuilderOrigin.BETA)
        report = self.engine.compare([c_alpha], [c_beta])
        assert len(report.diffs) >= 1

    def test_report_high_medium_low_partitions(self):
        c = _claim("A", "CONTRIBUTES_TO", "B")
        report = self.engine.compare([c], [])
        total = len(report.high) + len(report.medium) + len(report.low)
        assert total == len(report.diffs)
