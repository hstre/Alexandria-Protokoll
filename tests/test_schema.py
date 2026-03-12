"""
tests/test_schema.py — Unit tests for alexandria_core.schema

Run:
    python -m pytest tests/
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from alexandria_core.schema import (
    ClaimNode,
    Category,
    Modality,
    EpistemicStatus,
    BuilderOrigin,
    EntityNode,
    Uncertainty,
    UncertaintyType,
)


class TestClaimNode:
    def _make_claim(self, **kwargs) -> ClaimNode:
        defaults = dict(
            subject="Paris",
            predicate="RELATES_TO",
            object="France",
            category=Category.EMPIRICAL,
            assumptions=["Geography is stable"],
            source_refs=["openalex:W1234"],
        )
        defaults.update(kwargs)
        return ClaimNode.new(**defaults)

    def test_claim_has_uuid(self):
        c = self._make_claim()
        assert len(c.claim_id) == 36    # UUID4 format

    def test_default_status_is_unvalidated(self):
        c = self._make_claim()
        assert c.status == EpistemicStatus.UNVALIDATED

    def test_to_dict_round_trip(self):
        c = self._make_claim()
        d = c.to_dict()
        assert d["subject"] == "Paris"
        assert d["predicate"] == "RELATES_TO"
        assert d["category"] == "EMPIRICAL"

    def test_empirical_claim_accepts_uncertainty(self):
        u = Uncertainty(sigma=0.1, ci=(0.05, 0.15), n=100)
        c = self._make_claim(uncertainty=u)
        assert c.uncertainty is not None
        assert c.uncertainty.sigma == pytest.approx(0.1)

    def test_claim_timestamps_are_positive(self):
        c = self._make_claim()
        assert c.created_at > 0
        assert c.updated_at > 0

    def test_lineage_is_empty_on_creation(self):
        c = self._make_claim()
        assert c.lineage == []

    def test_two_claims_have_different_ids(self):
        c1 = self._make_claim()
        c2 = self._make_claim()
        assert c1.claim_id != c2.claim_id


class TestEntityNode:
    def test_entity_node_creation(self):
        e = EntityNode.new(name="Paris", entity_type="LOCATION")
        assert e.entity_id is not None
        assert e.name == "Paris"
        assert e.entity_type == "LOCATION"


class TestCategoryEnum:
    def test_all_four_categories_exist(self):
        for name in ("EMPIRICAL", "NORMATIVE", "MODEL", "SPECULATIVE"):
            assert Category[name] is not None

    def test_category_values_are_strings(self):
        for cat in Category:
            assert isinstance(cat.value, str)
