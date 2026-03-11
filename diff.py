"""
Alexandria Core — diff.py
Diff Engine (Section V-A.6, Technical Annex B v2)

The Diff Engine compares G_alpha and G_beta — the two independently
constructed claim graphs. Every divergence is classified as a typed
Diff and stored as a DiffNode in the graph.

Key design decision (per user feedback):
    A Diff is not just a report. It is a first-class graph object
    with its own lifecycle (OPEN → RESOLVED → ARCHIVED).
    This makes the diff history auditable, queryable, and usable
    for bias detection and Builder training.

Diff taxonomy: Technical Annex B v2
    B.1  Entity Diffs
    B.2  Concept Diffs
    B.3  Claim Diffs
    B.4  Relation / Causal Diffs
    B.5  Evidence Diffs
    B.6  Uncertainty Diffs
    B.7  Status Diffs
    B.8  Assumption Diffs
    B.9  Category Diffs
"""

from __future__ import annotations

import time
import uuid
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .schema import (
    ClaimNode, Category, Modality, EpistemicStatus,
    BuilderOrigin, RelationType, Uncertainty,
    causal_priority,
)

log = logging.getLogger(__name__)


# ── Diff type taxonomy (Annex B v2) ───────────────────────────────────────────

class DiffType(str, Enum):
    """
    Complete diff type taxonomy from Technical Annex B v2.
    Each type maps to exactly one Adjudication rule in Annex C.
    """
    # B.1 Entity Diffs
    MISSING_ENTITY          = "missing_entity"
    ENTITY_ALIAS_MISMATCH   = "entity_alias_mismatch"
    ENTITY_TYPE_MISMATCH    = "entity_type_mismatch"

    # B.2 Concept Diffs
    CONCEPT_MAPPING_MISMATCH    = "concept_mapping_mismatch"
    CONCEPT_HIERARCHY_MISMATCH  = "concept_hierarchy_mismatch"
    CONCEPT_SCOPE_MISMATCH      = "concept_scope_mismatch"

    # B.3 Claim Diffs
    MISSING_CLAIM           = "missing_claim"
    GRANULARITY_MISMATCH    = "granularity_mismatch"
    CLAIM_SCOPE_MISMATCH    = "claim_scope_mismatch"
    MODALITY_MISMATCH       = "modality_mismatch"
    QUANTIFIER_MISMATCH     = "quantifier_mismatch"
    TEMPORAL_MISMATCH       = "temporal_mismatch"

    # B.4 Relation / Causal Diffs
    RELATION_MISMATCH       = "relation_mismatch"
    DIRECTION_MISMATCH      = "direction_mismatch"
    CAUSALITY_MISMATCH      = "causality_mismatch"   # one reads causal, other reads correlational

    # B.5 Evidence Diffs
    SOURCE_LINK_MISMATCH        = "source_link_mismatch"
    EVIDENCE_STRENGTH_MISMATCH  = "evidence_strength_mismatch"
    CITATION_SCOPE_MISMATCH     = "citation_scope_mismatch"

    # B.6 Uncertainty Diffs
    UNCERTAINTY_DIVERGENCE_MINOR = "uncertainty_divergence_minor"  # sigma diff < 0.15
    UNCERTAINTY_DIVERGENCE_MAJOR = "uncertainty_divergence_major"  # sigma diff >= 0.15
    UNCERTAINTY_TYPE_MISMATCH    = "uncertainty_type_mismatch"     # probabilistic vs deterministic

    # B.7 Status Diffs
    STATUS_MISMATCH         = "status_mismatch"

    # B.8 Assumption Diffs
    ASSUMPTION_MISMATCH         = "assumption_mismatch"
    ASSUMPTION_SCOPE_MISMATCH   = "assumption_scope_mismatch"

    # B.9 Category Diffs
    CATEGORY_MISMATCH       = "category_mismatch"


class DiffSeverity(str, Enum):
    """
    Operational severity — determines adjudication priority.
    HIGH = blocks sealing. MEDIUM = requires resolution. LOW = advisory.
    """
    HIGH   = "HIGH"    # category_mismatch, assumption_mismatch, causality_mismatch
    MEDIUM = "MEDIUM"  # modality_mismatch, uncertainty_divergence_major, relation_mismatch
    LOW    = "LOW"     # minor divergences, advisory notes


class DiffStatus(str, Enum):
    """Lifecycle of a DiffNode in the graph."""
    OPEN       = "OPEN"       # detected, not yet adjudicated
    RESOLVED   = "RESOLVED"   # adjudication produced a winner or refinement
    BRANCHED   = "BRANCHED"   # unresolvable — branch triggered
    ARCHIVED   = "ARCHIVED"   # resolved + sealed, kept for audit history


# Severity mapping per diff type
DIFF_SEVERITY: dict[DiffType, DiffSeverity] = {
    # HIGH — blocks sealing
    DiffType.CATEGORY_MISMATCH:          DiffSeverity.HIGH,
    DiffType.ASSUMPTION_MISMATCH:        DiffSeverity.HIGH,
    DiffType.ASSUMPTION_SCOPE_MISMATCH:  DiffSeverity.HIGH,
    DiffType.CAUSALITY_MISMATCH:         DiffSeverity.HIGH,
    DiffType.MISSING_CLAIM:              DiffSeverity.HIGH,

    # MEDIUM — requires resolution
    DiffType.RELATION_MISMATCH:              DiffSeverity.MEDIUM,
    DiffType.MODALITY_MISMATCH:              DiffSeverity.MEDIUM,
    DiffType.UNCERTAINTY_DIVERGENCE_MAJOR:   DiffSeverity.MEDIUM,
    DiffType.UNCERTAINTY_TYPE_MISMATCH:      DiffSeverity.MEDIUM,
    DiffType.STATUS_MISMATCH:                DiffSeverity.MEDIUM,
    DiffType.DIRECTION_MISMATCH:             DiffSeverity.MEDIUM,
    DiffType.EVIDENCE_STRENGTH_MISMATCH:     DiffSeverity.MEDIUM,

    # LOW — advisory
    DiffType.UNCERTAINTY_DIVERGENCE_MINOR:   DiffSeverity.LOW,
    DiffType.GRANULARITY_MISMATCH:           DiffSeverity.LOW,
    DiffType.CLAIM_SCOPE_MISMATCH:           DiffSeverity.LOW,
    DiffType.TEMPORAL_MISMATCH:              DiffSeverity.LOW,
    DiffType.QUANTIFIER_MISMATCH:            DiffSeverity.LOW,
    DiffType.SOURCE_LINK_MISMATCH:           DiffSeverity.LOW,
    DiffType.CITATION_SCOPE_MISMATCH:        DiffSeverity.LOW,
    DiffType.ENTITY_ALIAS_MISMATCH:          DiffSeverity.LOW,
    DiffType.ENTITY_TYPE_MISMATCH:           DiffSeverity.LOW,
    DiffType.CONCEPT_MAPPING_MISMATCH:       DiffSeverity.LOW,
    DiffType.CONCEPT_HIERARCHY_MISMATCH:     DiffSeverity.LOW,
    DiffType.CONCEPT_SCOPE_MISMATCH:         DiffSeverity.LOW,
    DiffType.MISSING_ENTITY:                 DiffSeverity.LOW,
}


# ── DiffNode — first-class graph object ───────────────────────────────────────

@dataclass
class DiffNode:
    """
    A typed, versioned divergence between Builder Alpha and Builder Beta.

    This is a first-class graph object, not just a report entry.
    Stored in Neo4j as a :Diff node with relations to the two
    source claims and (after adjudication) to the resolved claim.

    Lifecycle:
        OPEN → RESOLVED  (adjudication chose a winner or refinement)
        OPEN → BRANCHED  (unresolvable — STABLE_AMBIGUITY triggered)
        RESOLVED/BRANCHED → ARCHIVED  (after sealing)

    Value for the system:
        - Auditable diff history
        - Bias detection: recurring diff types from same builder = systematic bias
        - Builder training: diffs are labeled examples of extraction errors
        - Graph maturity: DiffResolutionRate metric (Annex G)
    """
    diff_id:        str
    diff_type:      DiffType
    severity:       DiffSeverity

    # Source nodes being compared
    claim_alpha_id: str
    claim_beta_id:  Optional[str]  # None for MISSING_CLAIM diffs

    # What specifically diverges
    field_name:     str            # e.g. "predicate", "category", "uncertainty.sigma"
    value_alpha:    str            # string representation of Alpha's value
    value_beta:     str            # string representation of Beta's value

    # Human-readable description
    description:    str

    # Lifecycle
    status:         DiffStatus = DiffStatus.OPEN
    resolution_id:  Optional[str] = None   # judgment_id or patch_id that resolved this
    resolved_at:    Optional[float] = None

    # Source reference
    source_ref:     str = ""       # work_id or concept_id being processed

    # Sprint 3: Bias analysis metadata (v2.2)
    # These fields enable BuilderBiasAnalyzer to detect systematic extraction errors.
    # [DBA] — not protocol-required, but essential for quality feedback loop.
    adjudication_rule: Optional[str] = None  # which rule resolved/failed (e.g. "C.7")
    winning_builder:   Optional[str] = None  # "alpha" | "beta" | None (ambiguous)
    bias_tag:          Optional[str] = None  # e.g. "causal_overreach", "scope_narrowing"

    created_at:     float = field(default_factory=time.time)

    @classmethod
    def new(
        cls,
        diff_type:      DiffType,
        claim_alpha_id: str,
        claim_beta_id:  Optional[str],
        field_name:     str,
        value_alpha:    str,
        value_beta:     str,
        description:    str,
        source_ref:     str = "",
    ) -> "DiffNode":
        return cls(
            diff_id        = str(uuid.uuid4()),
            diff_type      = diff_type,
            severity       = DIFF_SEVERITY.get(diff_type, DiffSeverity.LOW),
            claim_alpha_id = claim_alpha_id,
            claim_beta_id  = claim_beta_id,
            field_name     = field_name,
            value_alpha    = value_alpha,
            value_beta     = value_beta,
            description    = description,
            source_ref     = source_ref,
        )

    def resolve(self, resolution_id: str, branch: bool = False):
        """Mark this diff as resolved or branched."""
        self.status       = DiffStatus.BRANCHED if branch else DiffStatus.RESOLVED
        self.resolution_id = resolution_id
        self.resolved_at  = time.time()

    def archive(self):
        self.status = DiffStatus.ARCHIVED

    def to_dict(self) -> dict:
        return {
            "diff_id":        self.diff_id,
            "diff_type":      self.diff_type.value,
            "severity":       self.severity.value,
            "claim_alpha_id": self.claim_alpha_id,
            "claim_beta_id":  self.claim_beta_id or "",
            "field_name":     self.field_name,
            "value_alpha":    self.value_alpha,
            "value_beta":     self.value_beta,
            "description":    self.description,
            "status":         self.status.value,
            "resolution_id":  self.resolution_id or "",
            "resolved_at":    self.resolved_at or 0.0,
            "source_ref":     self.source_ref,
            "adjudication_rule": self.adjudication_rule or "",
            "winning_builder":   self.winning_builder or "",
            "bias_tag":          self.bias_tag or "",
            "created_at":     self.created_at,
        }


# ── Diff Report ───────────────────────────────────────────────────────────────

@dataclass
class DiffReport:
    """
    Complete typed diff report for one source document.
    Input to the Adjudication layer.

    Contains all DiffNodes found between G_alpha and G_beta.
    Structured by severity for prioritized adjudication.
    """
    source_ref:   str
    diffs:        list[DiffNode] = field(default_factory=list)
    created_at:   float = field(default_factory=time.time)

    @property
    def high(self) -> list[DiffNode]:
        return [d for d in self.diffs if d.severity == DiffSeverity.HIGH]

    @property
    def medium(self) -> list[DiffNode]:
        return [d for d in self.diffs if d.severity == DiffSeverity.MEDIUM]

    @property
    def low(self) -> list[DiffNode]:
        return [d for d in self.diffs if d.severity == DiffSeverity.LOW]

    @property
    def open_diffs(self) -> list[DiffNode]:
        return [d for d in self.diffs if d.status == DiffStatus.OPEN]

    @property
    def blocks_sealing(self) -> bool:
        """True if any HIGH-severity diffs are unresolved."""
        return any(
            d.severity == DiffSeverity.HIGH and d.status == DiffStatus.OPEN
            for d in self.diffs
        )

    def summary(self) -> str:
        lines = [
            f"DiffReport — source={self.source_ref}",
            f"  Total diffs:  {len(self.diffs)}",
            f"  HIGH:         {len(self.high)}",
            f"  MEDIUM:       {len(self.medium)}",
            f"  LOW:          {len(self.low)}",
            f"  Open:         {len(self.open_diffs)}",
            f"  Blocks seal:  {self.blocks_sealing}",
        ]
        return "\n".join(lines)

    def by_type(self) -> dict[str, list[DiffNode]]:
        """Group diffs by type for analysis."""
        result: dict[str, list[DiffNode]] = {}
        for d in self.diffs:
            result.setdefault(d.diff_type.value, []).append(d)
        return result


# ── Diff Engine ───────────────────────────────────────────────────────────────

class DiffEngine:
    """
    Computes typed DiffNodes between G_alpha and G_beta.

    Matching strategy:
        Claims are matched by (subject, predicate_family, object) similarity.
        Predicate family: causal-scale predicates are grouped together
        because Alpha might read CORRELATES_WITH where Beta reads SUPPORTS —
        that's a relation_mismatch, not two unrelated claims.

    The engine does NOT resolve diffs. It only classifies them.
    Resolution is the Adjudication layer's responsibility.

    Usage:
        engine = DiffEngine()
        report = engine.compare(claims_alpha, claims_beta, source_ref="W123")
    """

    # How close sigma values need to be before it's a MINOR vs MAJOR diff
    SIGMA_MAJOR_THRESHOLD = 0.15

    def compare(
        self,
        claims_alpha: list[ClaimNode],
        claims_beta:  list[ClaimNode],
        source_ref:   str = "",
    ) -> DiffReport:
        """
        Full comparison of two claim sets.
        Returns a DiffReport with all typed DiffNodes.
        """
        report = DiffReport(source_ref=source_ref)

        # Match claims between alpha and beta
        pairs, unmatched_alpha, unmatched_beta = self._match_claims(
            claims_alpha, claims_beta
        )

        log.info(
            f"DiffEngine: {len(pairs)} matched pairs, "
            f"{len(unmatched_alpha)} alpha-only, "
            f"{len(unmatched_beta)} beta-only"
        )

        # Compare matched pairs
        for ca, cb in pairs:
            diffs = self._compare_pair(ca, cb, source_ref)
            report.diffs.extend(diffs)

        # Unmatched alpha claims = MISSING from beta
        for ca in unmatched_alpha:
            report.diffs.append(DiffNode.new(
                diff_type      = DiffType.MISSING_CLAIM,
                claim_alpha_id = ca.claim_id,
                claim_beta_id  = None,
                field_name     = "claim",
                value_alpha    = f"{ca.subject}-{ca.predicate}-{ca.object}",
                value_beta     = "(absent)",
                description    = (
                    f"Alpha found claim {ca.subject!r} {ca.predicate} {ca.object!r} "
                    f"— not present in Beta graph."
                ),
                source_ref     = source_ref,
            ))

        # Unmatched beta claims = MISSING from alpha
        for cb in unmatched_beta:
            report.diffs.append(DiffNode.new(
                diff_type      = DiffType.MISSING_CLAIM,
                claim_alpha_id = cb.claim_id,   # using beta id here, noted in description
                claim_beta_id  = None,
                field_name     = "claim",
                value_alpha    = "(absent)",
                value_beta     = f"{cb.subject}-{cb.predicate}-{cb.object}",
                description    = (
                    f"Beta found claim {cb.subject!r} {cb.predicate} {cb.object!r} "
                    f"— not present in Alpha graph."
                ),
                source_ref     = source_ref,
            ))

        log.info(
            f"DiffEngine: {len(report.diffs)} total diffs "
            f"({len(report.high)} HIGH, {len(report.medium)} MEDIUM, "
            f"{len(report.low)} LOW)"
        )
        return report

    # ── Claim matching ────────────────────────────────────────────────────────

    def _match_claims(
        self,
        alpha: list[ClaimNode],
        beta:  list[ClaimNode],
    ) -> tuple[list[tuple[ClaimNode, ClaimNode]], list[ClaimNode], list[ClaimNode]]:
        """
        Match claims between alpha and beta by semantic similarity.

        Matching priority:
        1. Exact: same subject + predicate + object
        2. Causal-family: same subject + object, predicates in same causal family
        3. Subject-object: same subject + object, different predicate

        Unmatched claims from either side become MISSING_CLAIM diffs.
        """
        pairs: list[tuple[ClaimNode, ClaimNode]] = []
        used_beta: set[str] = set()

        for ca in alpha:
            best_match = self._find_best_match(ca, beta, used_beta)
            if best_match:
                pairs.append((ca, best_match))
                used_beta.add(best_match.claim_id)

        unmatched_alpha = [
            ca for ca in alpha
            if not any(ca.claim_id == p[0].claim_id for p in pairs)
        ]
        unmatched_beta = [
            cb for cb in beta
            if cb.claim_id not in used_beta
        ]

        return pairs, unmatched_alpha, unmatched_beta

    def _find_best_match(
        self,
        ca:        ClaimNode,
        beta:      list[ClaimNode],
        used_beta: set[str],
    ) -> Optional[ClaimNode]:
        """Find the best Beta match for an Alpha claim."""
        candidates = [cb for cb in beta if cb.claim_id not in used_beta]

        # 1. Exact match
        for cb in candidates:
            if (self._norm(ca.subject)   == self._norm(cb.subject) and
                self._norm(ca.predicate) == self._norm(cb.predicate) and
                self._norm(ca.object)    == self._norm(cb.object)):
                return cb

        # 2. Causal-family match (same subject/object, predicates on same scale)
        for cb in candidates:
            if (self._norm(ca.subject) == self._norm(cb.subject) and
                self._norm(ca.object)  == self._norm(cb.object) and
                causal_priority(ca.predicate) >= 0 and
                causal_priority(cb.predicate) >= 0):
                return cb

        # 3. Subject-object match (any predicate)
        for cb in candidates:
            if (self._norm(ca.subject) == self._norm(cb.subject) and
                self._norm(ca.object)  == self._norm(cb.object)):
                return cb

        return None

    @staticmethod
    def _norm(s: str) -> str:
        """Normalize string for matching."""
        return s.strip().lower().replace(" ", "_").replace("-", "_")

    # ── Pair comparison ───────────────────────────────────────────────────────

    def _compare_pair(
        self,
        ca: ClaimNode,
        cb: ClaimNode,
        source_ref: str,
    ) -> list[DiffNode]:
        """
        Compare a matched Alpha/Beta pair.
        Returns list of DiffNodes for all divergences found.
        """
        diffs: list[DiffNode] = []

        # B.9 — Category (checked first — goes before C.7)
        if ca.category != cb.category:
            diffs.append(DiffNode.new(
                diff_type      = DiffType.CATEGORY_MISMATCH,
                claim_alpha_id = ca.claim_id,
                claim_beta_id  = cb.claim_id,
                field_name     = "category",
                value_alpha    = ca.category.value,
                value_beta     = cb.category.value,
                description    = (
                    f"Category mismatch: Alpha={ca.category.value}, "
                    f"Beta={cb.category.value}. "
                    "Category mixing is a structural violation (Annex C.8). "
                    "Goes before all other adjudication."
                ),
                source_ref     = source_ref,
            ))

        # B.8 — Assumptions
        alpha_assumptions = set(ca.assumptions)
        beta_assumptions  = set(cb.assumptions)
        if alpha_assumptions != beta_assumptions:
            only_alpha = alpha_assumptions - beta_assumptions
            only_beta  = beta_assumptions  - alpha_assumptions
            # Check if this is a scope-level or content-level mismatch
            dt = (DiffType.ASSUMPTION_SCOPE_MISMATCH
                  if self._is_scope_assumption_diff(only_alpha, only_beta)
                  else DiffType.ASSUMPTION_MISMATCH)
            diffs.append(DiffNode.new(
                diff_type      = dt,
                claim_alpha_id = ca.claim_id,
                claim_beta_id  = cb.claim_id,
                field_name     = "assumptions",
                value_alpha    = str(sorted(only_alpha)),
                value_beta     = str(sorted(only_beta)),
                description    = (
                    f"Assumption divergence: "
                    f"only_alpha={sorted(only_alpha)}, "
                    f"only_beta={sorted(only_beta)}. "
                    "Note: CONTRADICTS between claims with different assumptions "
                    "is an assumption_mismatch, not a genuine contradiction (Annex F.3)."
                ),
                source_ref     = source_ref,
            ))

        # B.4 — Relation / Causal
        if ca.predicate != cb.predicate:
            prio_a = causal_priority(ca.predicate)
            prio_b = causal_priority(cb.predicate)

            if prio_a >= 0 and prio_b >= 0:
                # Both on causal scale
                if abs(prio_a - prio_b) >= 3:
                    # Large gap: one reads causal, other reads correlational
                    dt = DiffType.CAUSALITY_MISMATCH
                else:
                    dt = DiffType.RELATION_MISMATCH
            else:
                dt = DiffType.RELATION_MISMATCH

            diffs.append(DiffNode.new(
                diff_type      = dt,
                claim_alpha_id = ca.claim_id,
                claim_beta_id  = cb.claim_id,
                field_name     = "predicate",
                value_alpha    = ca.predicate,
                value_beta     = cb.predicate,
                description    = (
                    f"Predicate divergence: Alpha={ca.predicate!r}, "
                    f"Beta={cb.predicate!r}. "
                    f"Adjudication: C.7 (defensive priority wins)."
                ),
                source_ref     = source_ref,
            ))

        # B.3 — Modality
        if ca.modality != cb.modality:
            diffs.append(DiffNode.new(
                diff_type      = DiffType.MODALITY_MISMATCH,
                claim_alpha_id = ca.claim_id,
                claim_beta_id  = cb.claim_id,
                field_name     = "modality",
                value_alpha    = ca.modality.value,
                value_beta     = cb.modality.value,
                description    = (
                    f"Modality divergence: Alpha={ca.modality.value}, "
                    f"Beta={cb.modality.value}. "
                    "Adjudication: C.1 (weaker modality wins)."
                ),
                source_ref     = source_ref,
            ))

        # B.6 — Uncertainty
        unc_diffs = self._compare_uncertainty(ca, cb, source_ref)
        diffs.extend(unc_diffs)

        # B.3 — Temporal scope
        if ca.time_scope != cb.time_scope and (ca.time_scope or cb.time_scope):
            diffs.append(DiffNode.new(
                diff_type      = DiffType.TEMPORAL_MISMATCH,
                claim_alpha_id = ca.claim_id,
                claim_beta_id  = cb.claim_id,
                field_name     = "time_scope",
                value_alpha    = str(ca.time_scope),
                value_beta     = str(cb.time_scope),
                description    = (
                    f"Temporal scope divergence: "
                    f"Alpha={ca.time_scope}, Beta={cb.time_scope}."
                ),
                source_ref     = source_ref,
            ))

        # B.3 — Claim scope
        if ca.scope != cb.scope and (ca.scope or cb.scope):
            diffs.append(DiffNode.new(
                diff_type      = DiffType.CLAIM_SCOPE_MISMATCH,
                claim_alpha_id = ca.claim_id,
                claim_beta_id  = cb.claim_id,
                field_name     = "scope",
                value_alpha    = str(ca.scope),
                value_beta     = str(cb.scope),
                description    = (
                    f"Scope divergence: Alpha={ca.scope}, Beta={cb.scope}."
                ),
                source_ref     = source_ref,
            ))

        # B.5 — Evidence sources
        alpha_sources = set(ca.source_refs + ca.evidence_refs)
        beta_sources  = set(cb.source_refs + cb.evidence_refs)
        if alpha_sources != beta_sources:
            diffs.append(DiffNode.new(
                diff_type      = DiffType.SOURCE_LINK_MISMATCH,
                claim_alpha_id = ca.claim_id,
                claim_beta_id  = cb.claim_id,
                field_name     = "source_refs",
                value_alpha    = str(sorted(alpha_sources)),
                value_beta     = str(sorted(beta_sources)),
                description    = "Source reference divergence.",
                source_ref     = source_ref,
            ))

        return diffs

    def _compare_uncertainty(
        self,
        ca: ClaimNode,
        cb: ClaimNode,
        source_ref: str,
    ) -> list[DiffNode]:
        """B.6 — Uncertainty comparisons."""
        diffs = []

        has_a = ca.uncertainty is not None
        has_b = cb.uncertainty is not None

        # Type mismatch: one has uncertainty, other doesn't
        if has_a != has_b:
            diffs.append(DiffNode.new(
                diff_type      = DiffType.UNCERTAINTY_TYPE_MISMATCH,
                claim_alpha_id = ca.claim_id,
                claim_beta_id  = cb.claim_id,
                field_name     = "uncertainty",
                value_alpha    = "present" if has_a else "absent",
                value_beta     = "present" if has_b else "absent",
                description    = (
                    "One builder produced probabilistic uncertainty, "
                    "the other did not. "
                    "Non-aggregation applies (Section VII.4): "
                    "larger sigma wins."
                ),
                source_ref     = source_ref,
            ))
            return diffs

        if not has_a and not has_b:
            return diffs  # both deterministic — no diff

        # Both have uncertainty — compare sigma
        ua = ca.uncertainty
        ub = cb.uncertainty
        sigma_diff = abs(ua.sigma - ub.sigma)

        if sigma_diff > 0.001:  # meaningful difference
            dt = (DiffType.UNCERTAINTY_DIVERGENCE_MAJOR
                  if sigma_diff >= self.SIGMA_MAJOR_THRESHOLD
                  else DiffType.UNCERTAINTY_DIVERGENCE_MINOR)
            diffs.append(DiffNode.new(
                diff_type      = dt,
                claim_alpha_id = ca.claim_id,
                claim_beta_id  = cb.claim_id,
                field_name     = "uncertainty.sigma",
                value_alpha    = f"σ={ua.sigma} CI={ua.ci} n={ua.n}",
                value_beta     = f"σ={ub.sigma} CI={ub.ci} n={ub.n}",
                description    = (
                    f"Sigma divergence: Δσ={sigma_diff:.3f}. "
                    "Non-aggregation rule (Section VII.4): "
                    f"larger sigma preserved (σ={max(ua.sigma, ub.sigma)})."
                ),
                source_ref     = source_ref,
            ))

        return diffs

    @staticmethod
    def _is_scope_assumption_diff(only_a: set[str], only_b: set[str]) -> bool:
        """
        Heuristic: if diverging assumptions are mostly scope-markers
        (contain 'Scope', 'Region', 'Population', 'Temporal'),
        classify as ASSUMPTION_SCOPE_MISMATCH rather than ASSUMPTION_MISMATCH.
        """
        scope_markers = {"scope", "region", "population", "temporal", "domain"}
        all_diverging = only_a | only_b
        scope_count   = sum(
            1 for a in all_diverging
            if any(m in a.lower() for m in scope_markers)
        )
        return scope_count > len(all_diverging) / 2


# ── Bias analysis helper ───────────────────────────────────────────────────────

class BuilderBiasAnalyzer:
    """
    Analyzes recurring diff patterns to detect systematic Builder bias.

    If Alpha consistently reads stronger causal relations than Beta,
    that is a systematic bias, not random divergence.
    This information is valuable for Builder training.

    Usage:
        analyzer = BuilderBiasAnalyzer()
        analyzer.add_report(report1)
        analyzer.add_report(report2)
        print(analyzer.summary())
    """

    def __init__(self):
        self._reports: list[DiffReport] = []

    def add_report(self, report: DiffReport):
        self._reports.append(report)

    @property
    def total_diffs(self) -> int:
        return sum(len(r.diffs) for r in self._reports)

    def type_frequencies(self) -> dict[str, int]:
        """Count occurrences of each diff type across all reports."""
        freq: dict[str, int] = {}
        for report in self._reports:
            for diff in report.diffs:
                freq[diff.diff_type.value] = freq.get(diff.diff_type.value, 0) + 1
        return dict(sorted(freq.items(), key=lambda x: -x[1]))

    def causal_bias(self) -> dict:
        """
        Detect systematic causal scale bias.
        Returns dict with alpha_stronger_count, beta_stronger_count, bias_direction.
        """
        alpha_stronger = 0
        beta_stronger  = 0

        for report in self._reports:
            for diff in report.diffs:
                if diff.diff_type in (
                    DiffType.RELATION_MISMATCH, DiffType.CAUSALITY_MISMATCH
                ):
                    prio_a = causal_priority(diff.value_alpha)
                    prio_b = causal_priority(diff.value_beta)
                    if prio_a > prio_b:
                        alpha_stronger += 1
                    elif prio_b > prio_a:
                        beta_stronger += 1

        total = alpha_stronger + beta_stronger
        if total == 0:
            return {"alpha_stronger": 0, "beta_stronger": 0,
                    "bias_direction": "none", "confidence": 0.0}

        bias = "alpha" if alpha_stronger > beta_stronger else "beta"
        confidence = max(alpha_stronger, beta_stronger) / total

        return {
            "alpha_stronger":  alpha_stronger,
            "beta_stronger":   beta_stronger,
            "bias_direction":  bias if confidence > 0.6 else "balanced",
            "confidence":      round(confidence, 3),
        }

    def summary(self) -> str:
        lines = [
            f"BuilderBiasAnalyzer — {len(self._reports)} reports, "
            f"{self.total_diffs} total diffs",
            "",
            "Top diff types:",
        ]
        for dt, count in list(self.type_frequencies().items())[:8]:
            lines.append(f"  {dt:<40} {count}")

        bias = self.causal_bias()
        lines += [
            "",
            f"Causal bias: {bias['bias_direction']} "
            f"(α_stronger={bias['alpha_stronger']}, "
            f"β_stronger={bias['beta_stronger']}, "
            f"confidence={bias['confidence']})",
        ]
        return "\n".join(lines)
