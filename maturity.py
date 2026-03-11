"""
Alexandria Core — maturity.py
Graph Maturity Metrics (Technical Annex G)

Answers the question: when is an Alexandria graph "mature enough"?

Five metrics (Annex G.2):
    M1  GraphDensity           — structural coverage
    M2  ClaimStability         — temporal epistemic stability (extends XI.4)
    M3  DiffResolutionRate     — adjudication completeness
    M4  EvidenceCoverage       — what fraction of claims have evidence
    M5  TemporalPersistence    — how long claims survive without revision

Composite maturity score Φ (Annex G.3):
    Φ = w1·M1 + w2·M2 + w3·M3 + w4·M4 + w5·M5
    Default weights: w = [0.15, 0.25, 0.25, 0.20, 0.15]

Maturity thresholds (Annex G.4):
    Φ < 0.40  → IMMATURE     — not ready for any downstream use
    Φ < 0.65  → DEVELOPING   — usable for exploration, not for sealing
    Φ < 0.85  → FUNCTIONAL   — ready for VALIDATED status
    Φ ≥ 0.85  → MATURE       — ready for SEALED status
"""

from __future__ import annotations

import math
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .schema import (
    ClaimNode, EpistemicStatus, Category, Modality,
)
from .diff import DiffReport, DiffStatus, DiffNode
from .patch import PatchChain

log = logging.getLogger(__name__)


# ── Maturity thresholds (Annex G.4) ──────────────────────────────────────────

class MaturityLevel(str, Enum):
    IMMATURE   = "IMMATURE"    # Φ < 0.40
    DEVELOPING = "DEVELOPING"  # 0.40 ≤ Φ < 0.65
    FUNCTIONAL = "FUNCTIONAL"  # 0.65 ≤ Φ < 0.85
    MATURE     = "MATURE"      # Φ ≥ 0.85

MATURITY_THRESHOLDS = [
    (0.85, MaturityLevel.MATURE),
    (0.65, MaturityLevel.FUNCTIONAL),
    (0.40, MaturityLevel.DEVELOPING),
    (0.00, MaturityLevel.IMMATURE),
]

def maturity_level(phi: float) -> MaturityLevel:
    for threshold, level in MATURITY_THRESHOLDS:
        if phi >= threshold:
            return level
    return MaturityLevel.IMMATURE


# ── Per-metric results ────────────────────────────────────────────────────────

@dataclass
class MetricResult:
    name:        str
    value:       float          # 0.0 – 1.0
    weight:      float
    weighted:    float          # value × weight
    description: str
    components:  dict = field(default_factory=dict)  # sub-values for transparency

    def __str__(self) -> str:
        bar = "█" * int(self.value * 20) + "░" * (20 - int(self.value * 20))
        return (
            f"  {self.name:<25} {bar} "
            f"{self.value:.3f} × w={self.weight:.2f} = {self.weighted:.4f}"
        )


@dataclass
class MaturityReport:
    """
    Complete graph maturity assessment.
    Immutable once produced — Annex G.5 (audit trail).
    """
    graph_id:    str
    timestamp:   float
    metrics:     list[MetricResult]
    phi:         float          # composite score
    level:       MaturityLevel
    ready_to_seal: bool
    notes:       list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [
            f"MaturityReport — graph={self.graph_id}",
            f"  Composite Φ = {self.phi:.4f}  →  {self.level.value}",
            f"  Ready to seal: {'YES' if self.ready_to_seal else 'NO'}",
            "",
            "  Metrics:",
        ]
        for m in self.metrics:
            lines.append(str(m))
        if self.notes:
            lines.append("")
            lines.append("  Notes:")
            for n in self.notes:
                lines.append(f"    · {n}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "graph_id":      self.graph_id,
            "timestamp":     self.timestamp,
            "phi":           self.phi,
            "level":         self.level.value,
            "ready_to_seal": self.ready_to_seal,
            "metrics": {
                m.name: {
                    "value":      m.value,
                    "weight":     m.weight,
                    "weighted":   m.weighted,
                    "components": m.components,
                }
                for m in self.metrics
            },
            "notes": self.notes,
        }


# ── Maturity Calculator ───────────────────────────────────────────────────────

class MaturityCalculator:
    """
    Computes graph maturity metrics from in-memory state.

    Usage:
        calc = MaturityCalculator()
        report = calc.assess(
            claims        = all_claim_nodes,
            diff_reports  = all_diff_reports,
            patch_chain   = patch_chain,
            graph_id      = "snapshot_001",
        )
        print(report)

    For Neo4j-backed graphs, pass the result of db queries
    directly to the claims/diffs parameters.
    """

    DEFAULT_WEIGHTS = {
        "GraphDensity":        0.15,
        "ClaimStability":      0.25,
        "DiffResolutionRate":  0.25,
        "EvidenceCoverage":    0.20,
        "TemporalPersistence": 0.15,
    }

    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = weights or self.DEFAULT_WEIGHTS

    def assess(
        self,
        claims:       list[ClaimNode],
        diff_reports: list[DiffReport],
        patch_chain:  PatchChain | None = None,
        graph_id:     str = "graph",
        now:          float | None = None,
    ) -> MaturityReport:
        """
        Compute all five metrics and the composite Φ score.
        """
        now = now or time.time()
        notes: list[str] = []

        if not claims:
            return MaturityReport(
                graph_id=graph_id,
                timestamp=now,
                metrics=[],
                phi=0.0,
                level=MaturityLevel.IMMATURE,
                ready_to_seal=False,
                notes=["No claims in graph — cannot assess maturity."],
            )

        all_diffs = [d for r in diff_reports for d in r.diffs]

        m1 = self._m1_graph_density(claims)
        m2 = self._m2_claim_stability(claims, now)
        m3 = self._m3_diff_resolution_rate(all_diffs)
        m4 = self._m4_evidence_coverage(claims)
        m5 = self._m5_temporal_persistence(claims, patch_chain, now)

        metrics = [m1, m2, m3, m4, m5]
        phi = sum(m.weighted for m in metrics)
        level = maturity_level(phi)
        ready = level == MaturityLevel.MATURE

        # Automatic notes
        if m3.value < 0.5:
            notes.append(
                f"DiffResolutionRate={m3.value:.2f} — "
                "more than half of diffs unresolved. "
                "Adjudication incomplete."
            )
        if m4.value < 0.4:
            notes.append(
                f"EvidenceCoverage={m4.value:.2f} — "
                "most claims lack explicit evidence references. "
                "Source traceability (Annex D.2) at risk."
            )
        if m2.value < 0.5:
            notes.append(
                f"ClaimStability={m2.value:.2f} — "
                "high proportion of unstable or disputed claims. "
                "Consider additional validation passes."
            )

        formal_errors = [
            c for c in claims if c.status == EpistemicStatus.FORMAL_ERROR
        ]
        if formal_errors:
            notes.append(
                f"{len(formal_errors)} claim(s) in FORMAL_ERROR status. "
                "These block sealing regardless of Φ."
            )
            ready = False

        return MaturityReport(
            graph_id=graph_id,
            timestamp=now,
            metrics=metrics,
            phi=round(phi, 4),
            level=level,
            ready_to_seal=ready,
            notes=notes,
        )

    # ── M1: Graph Density (Annex G.2.1) ──────────────────────────────────────

    def _m1_graph_density(self, claims: list[ClaimNode]) -> MetricResult:
        """
        M1 = f(status_distribution, category_coverage, predicate_diversity)

        A dense graph has claims across all categories,
        uses a variety of relation types, and has a high
        proportion of non-UNVALIDATED claims.

        Formula:
            M1 = 0.4 · status_score
               + 0.3 · category_score
               + 0.3 · predicate_diversity
        """
        n = len(claims)

        # Status score: fraction not UNVALIDATED
        validated_count = sum(
            1 for c in claims
            if c.status not in (EpistemicStatus.UNVALIDATED, EpistemicStatus.FORMAL_ERROR)
        )
        status_score = validated_count / n

        # Category coverage: how many of the 4 categories are represented
        categories_present = len({c.category for c in claims})
        category_score = categories_present / 4.0

        # Predicate diversity: unique predicates / 8 (full causal scale)
        predicates_used = len({c.predicate for c in claims})
        predicate_diversity = min(predicates_used / 8.0, 1.0)

        value = (
            0.4 * status_score +
            0.3 * category_score +
            0.3 * predicate_diversity
        )
        w = self.weights["GraphDensity"]

        return MetricResult(
            name="GraphDensity",
            value=round(value, 4),
            weight=w,
            weighted=round(value * w, 4),
            description="Structural coverage: status distribution, category breadth, predicate diversity.",
            components={
                "n_claims":           n,
                "validated_fraction": round(status_score, 3),
                "categories_present": categories_present,
                "predicates_used":    predicates_used,
            },
        )

    # ── M2: Claim Stability (Annex G.2.2, extends XI.4) ──────────────────────

    def _m2_claim_stability(
        self, claims: list[ClaimNode], now: float
    ) -> MetricResult:
        """
        M2 = mean(S_k(t)) across all claims with validation.decay set.

        S_k(t) = e^(-λ_k · Δt)  where Δt = now - claim.created_at

        Claims without decay parameter contribute a fixed score of 0.5
        (unknown stability — neither stable nor unstable assumed).

        Heavily weighted (0.25) because temporal stability is the
        primary indicator of epistemic quality over time.
        """
        stability_scores = []

        for c in claims:
            if c.validation and c.validation.decay > 0:
                delta_t = max(now - c.created_at, 0)
                # Convert delta_t from seconds to "time units" (days)
                delta_days = delta_t / 86400.0
                s = math.exp(-c.validation.decay * delta_days)
                stability_scores.append(s)
            else:
                stability_scores.append(0.5)

        value = sum(stability_scores) / len(stability_scores) if stability_scores else 0.0
        with_decay = sum(1 for c in claims if c.validation and c.validation.decay > 0)
        w = self.weights["ClaimStability"]

        return MetricResult(
            name="ClaimStability",
            value=round(value, 4),
            weight=w,
            weighted=round(value * w, 4),
            description="Mean S_k(t) = e^(-λ·Δt) across claims. Extends Section XI.4.",
            components={
                "n_with_decay": with_decay,
                "n_no_decay":   len(claims) - with_decay,
                "mean_stability": round(value, 3),
            },
        )

    # ── M3: Diff Resolution Rate (Annex G.2.3) ───────────────────────────────

    def _m3_diff_resolution_rate(self, diffs: list[DiffNode]) -> MetricResult:
        """
        M3 = resolved_diffs / total_diffs

        Weighted by severity:
            HIGH diffs count 3×
            MEDIUM diffs count 2×
            LOW diffs count 1×

        An unresolved HIGH diff has 3× the negative impact of an
        unresolved LOW diff — reflecting the sealing criteria.

        M3 = 1.0 if no diffs exist (trivially consistent graph).
        """
        from .diff import DiffSeverity

        if not diffs:
            return MetricResult(
                name="DiffResolutionRate",
                value=1.0,
                weight=self.weights["DiffResolutionRate"],
                weighted=self.weights["DiffResolutionRate"],
                description="No diffs — trivially consistent.",
                components={"n_diffs": 0},
            )

        weight_map = {
            DiffSeverity.HIGH:   3,
            DiffSeverity.MEDIUM: 2,
            DiffSeverity.LOW:    1,
        }

        total_weight    = 0
        resolved_weight = 0

        for d in diffs:
            w = weight_map.get(d.severity, 1)
            total_weight += w
            if d.status in (DiffStatus.RESOLVED, DiffStatus.ARCHIVED, DiffStatus.BRANCHED):
                resolved_weight += w

        value = resolved_weight / total_weight if total_weight > 0 else 0.0
        w = self.weights["DiffResolutionRate"]

        high_open   = sum(1 for d in diffs if d.severity.value == "HIGH"   and d.status == DiffStatus.OPEN)
        medium_open = sum(1 for d in diffs if d.severity.value == "MEDIUM" and d.status == DiffStatus.OPEN)

        return MetricResult(
            name="DiffResolutionRate",
            value=round(value, 4),
            weight=w,
            weighted=round(value * w, 4),
            description="Weighted diff resolution: HIGH×3, MEDIUM×2, LOW×1.",
            components={
                "total_diffs":    len(diffs),
                "open_high":      high_open,
                "open_medium":    medium_open,
                "weighted_score": round(value, 3),
            },
        )

    # ── M4: Evidence Coverage (Annex G.2.4) ──────────────────────────────────

    def _m4_evidence_coverage(self, claims: list[ClaimNode]) -> MetricResult:
        """
        M4 = f(source_coverage, uncertainty_disclosure, assumption_coverage)

        source_coverage:       fraction of claims with ≥1 source_ref
        uncertainty_disclosure: fraction of empirical claims with uncertainty tuple
        assumption_coverage:   fraction of claims with ≥1 assumption

        Formula:
            M4 = 0.4 · source_coverage
               + 0.35 · uncertainty_disclosure
               + 0.25 · assumption_coverage

        Directly reflects Seal Criterion D.2 (Source Traceability)
        and Section VII.4 (Uncertainty Disclosure).
        """
        n = len(claims)

        source_coverage = sum(
            1 for c in claims if c.source_refs or c.evidence_refs
        ) / n

        empirical = [c for c in claims if c.category == Category.EMPIRICAL]
        if empirical:
            uncertainty_disclosure = sum(
                1 for c in empirical if c.uncertainty is not None
            ) / len(empirical)
        else:
            uncertainty_disclosure = 1.0  # no empirical claims = not applicable

        assumption_coverage = sum(
            1 for c in claims if c.assumptions
        ) / n

        value = (
            0.40 * source_coverage +
            0.35 * uncertainty_disclosure +
            0.25 * assumption_coverage
        )
        w = self.weights["EvidenceCoverage"]

        return MetricResult(
            name="EvidenceCoverage",
            value=round(value, 4),
            weight=w,
            weighted=round(value * w, 4),
            description="Source traceability, uncertainty disclosure, assumption coverage.",
            components={
                "source_coverage":        round(source_coverage, 3),
                "uncertainty_disclosure": round(uncertainty_disclosure, 3),
                "assumption_coverage":    round(assumption_coverage, 3),
                "n_empirical":            len(empirical),
            },
        )

    # ── M5: Temporal Persistence (Annex G.2.5) ───────────────────────────────

    def _m5_temporal_persistence(
        self,
        claims:      list[ClaimNode],
        patch_chain: PatchChain | None,
        now:         float,
    ) -> MetricResult:
        """
        M5 measures how long claims persist without being revised.

        If patch_chain is available:
            For each claim, compute time since last MODIFY patch.
            Longer unrevised = higher persistence score.
            Score = fraction of claims unrevised for > 7 days.

        If no patch_chain:
            Use created_at as proxy.
            Score = fraction of claims older than 1 day
            (heuristic: recently created graphs are less persistent).

        Normalized to [0, 1].
        """
        if patch_chain and patch_chain.length > 0:
            # Build map: claim_id → latest MODIFY patch timestamp
            from .schema import PatchOperation
            last_modified: dict[str, float] = {}
            for patch in patch_chain._patches:
                if patch.operation == PatchOperation.MODIFY:
                    tid = patch.target_id
                    if tid not in last_modified or patch.timestamp > last_modified[tid]:
                        last_modified[tid] = patch.timestamp

            persistence_scores = []
            for c in claims:
                last_mod = last_modified.get(c.claim_id, c.created_at)
                age_days  = (now - last_mod) / 86400.0
                # Score: logistic curve, saturates at 1 after ~30 days
                score = 1.0 - math.exp(-age_days / 10.0)
                persistence_scores.append(score)

            value = sum(persistence_scores) / len(persistence_scores)
            source = "patch_chain"

        else:
            # Fallback: fraction of claims older than 1 day
            one_day_ago = now - 86400.0
            old_enough  = sum(1 for c in claims if c.created_at < one_day_ago)
            value = old_enough / len(claims)
            source = "created_at_proxy"

        w = self.weights["TemporalPersistence"]

        return MetricResult(
            name="TemporalPersistence",
            value=round(value, 4),
            weight=w,
            weighted=round(value * w, 4),
            description="How long claims persist without revision. Logistic age curve.",
            components={
                "source":    source,
                "n_claims":  len(claims),
            },
        )


# ── Trend tracking ────────────────────────────────────────────────────────────

@dataclass
class MaturityTrend:
    """
    Tracks maturity reports over time to detect improvement or regression.
    Useful for monitoring a graph through its development lifecycle.

    Usage:
        trend = MaturityTrend()
        trend.add(report_1)
        trend.add(report_2)
        print(trend.summary())
    """
    reports: list[MaturityReport] = field(default_factory=list)

    def add(self, report: MaturityReport):
        self.reports.append(report)
        self.reports.sort(key=lambda r: r.timestamp)

    @property
    def latest(self) -> Optional[MaturityReport]:
        return self.reports[-1] if self.reports else None

    @property
    def phi_series(self) -> list[float]:
        return [r.phi for r in self.reports]

    def delta(self) -> Optional[float]:
        """Change in Φ between last two reports."""
        if len(self.reports) < 2:
            return None
        return self.reports[-1].phi - self.reports[-2].phi

    def is_regressing(self) -> bool:
        """True if last Φ is lower than second-to-last."""
        d = self.delta()
        return d is not None and d < -0.02

    def summary(self) -> str:
        if not self.reports:
            return "MaturityTrend: no reports."
        lines = [
            f"MaturityTrend — {len(self.reports)} snapshots",
            f"  Φ series: {[round(p,3) for p in self.phi_series]}",
        ]
        if len(self.reports) >= 2:
            d = self.delta()
            direction = "▲" if d > 0 else ("▼" if d < 0 else "=")
            lines.append(f"  Latest Δ:  {direction} {d:+.4f}")
        if self.is_regressing():
            lines.append("  ⚠ REGRESSION detected — Φ decreasing.")
        lines.append(f"  Current level: {self.latest.level.value}")
        return "\n".join(lines)
