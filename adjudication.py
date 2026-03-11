"""
Alexandria Core — adjudication.py
Adjudication Layer (Section V-A.7, Technical Annex C v2)

Receives a DiffReport, applies the Adjudication Rulebook,
produces an AdjudicationResult containing:
  - resolved ClaimNodes ready for patch emission
  - JudgmentNodes (one per adjudicated diff)
  - branch triggers for unresolvable diffs

Rule inventory (Annex C v2):
    C.1  Modality — weaker wins
    C.2  Missing claim — conservative inclusion
    C.3  Conflict cases — combined rules
    C.4  Evidence strength — more conservative wins
    C.5  Source link — union of sources
    C.6  Uncertainty — non-aggregative (larger sigma preserved)
    C.7  Relation type — defensive priority (Annex F.6 table)
    C.8  Category — goes before all others, no compromise
    C.9  Assumption — separation check before CONTRADICTS

Protocol invariants enforced here:
    - No averaging of uncertainty (Section VII.4)
    - No suppression of branches (Section VII.13.4)
    - Every decision produces a JudgmentNode (Section V-A.7)
    - Category mismatch = FORMAL_ERROR, no compromise (Annex C.8)
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .schema import (
    ClaimNode, JudgmentNode, BranchNode, MergePolicy,
    BuilderOrigin, Category, EpistemicStatus, Modality,
    Uncertainty, causal_priority,
)
from .diff import (
    DiffNode, DiffReport, DiffType, DiffSeverity, DiffStatus,
)

log = logging.getLogger(__name__)


# ── Adjudication outcomes ─────────────────────────────────────────────────────

class AdjudicationOutcome(str, Enum):
    CONVERGENCE     = "convergence"      # one claim adopted
    REFINEMENT      = "refinement"       # new claim derived from both
    STABLE_AMBIGUITY = "stable_ambiguity" # branch triggered
    FORMAL_ERROR    = "formal_error"     # structural violation, kept for audit


@dataclass
class RuleApplication:
    """Record of a single rule application to a diff."""
    rule:     str                  # e.g. "C.7", "C.8"
    diff_id:  str
    outcome:  AdjudicationOutcome
    reason:   str
    winning_id: Optional[str] = None   # claim_id of the winning claim


@dataclass
class AdjudicationResult:
    """
    Complete result of adjudicating one DiffReport.

    resolved_claims: ClaimNodes ready for PatchEmitter.add()
    judgments:       JudgmentNodes to store in the graph
    branch_triggers: BranchNode objects (First-Class, v2.2 Sprint 1)
    formal_errors:   diffs that constitute structural violations
    rule_log:        complete audit trail of all rule applications
    """
    source_ref:       str
    resolved_claims:  list[ClaimNode]    = field(default_factory=list)
    judgments:        list[JudgmentNode] = field(default_factory=list)
    branch_triggers:  list[BranchNode]   = field(default_factory=list)
    formal_errors:    list[DiffNode]     = field(default_factory=list)
    rule_log:         list[RuleApplication] = field(default_factory=list)
    created_at:       float              = field(default_factory=time.time)

    @property
    def has_formal_errors(self) -> bool:
        return len(self.formal_errors) > 0

    @property
    def branch_count(self) -> int:
        return len(self.branch_triggers)

    def summary(self) -> str:
        lines = [
            f"AdjudicationResult — source={self.source_ref}",
            f"  Resolved claims:  {len(self.resolved_claims)}",
            f"  Judgments:        {len(self.judgments)}",
            f"  Branches:         {self.branch_count}",
            f"  Formal errors:    {len(self.formal_errors)}",
            f"  Rules applied:    {len(self.rule_log)}",
        ]
        if self.rule_log:
            lines.append("")
            lines.append("  Rule log:")
            for r in self.rule_log:
                lines.append(
                    f"    {r.rule:<6} {r.outcome.value:<20} "
                    f"diff={r.diff_id[:8]}… "
                    f"{'winner='+r.winning_id[:8]+'…' if r.winning_id else ''}"
                )
        if self.branch_triggers:
            lines.append("")
            lines.append("  Branches:")
            for b in self.branch_triggers:
                lines.append(
                    f"    {b.branch_id[:8]}… "
                    f"reason={b.branch_reason[:60]} "
                    f"policy={b.merge_policy.value}"
                )
        return "\n".join(lines)


# ── Adjudicator ───────────────────────────────────────────────────────────────

class Adjudicator:
    """
    Applies the Annex C rulebook to a DiffReport.

    Processing order matters:
        1. C.8 — Category (structural, must go first)
        2. C.9 — Assumptions (affects whether CONTRADICTS is real)
        3. C.7 — Relation type (defensive priority)
        4. C.6 — Uncertainty (non-aggregative)
        5. C.1 — Modality
        6. C.4 — Evidence strength
        7. C.5 — Source links
        8. C.2 — Missing claims
        9. C.3 — Combined conflicts (last resort)

    Each rule produces a JudgmentNode and updates the DiffNode status.

    Usage:
        adjudicator = Adjudicator(claims_alpha, claims_beta)
        result = adjudicator.adjudicate(diff_report)
    """

    def __init__(
        self,
        claims_alpha: list[ClaimNode],
        claims_beta:  list[ClaimNode],
    ):
        # Index for fast lookup
        self._alpha: dict[str, ClaimNode] = {c.claim_id: c for c in claims_alpha}
        self._beta:  dict[str, ClaimNode] = {c.claim_id: c for c in claims_beta}

    def adjudicate(self, report: DiffReport) -> AdjudicationResult:
        """
        Adjudicate all diffs in the report.
        Returns AdjudicationResult with resolved claims and judgments.
        """
        result = AdjudicationResult(source_ref=report.source_ref)

        # Track which claim pairs have been processed
        # (multiple diffs can reference the same pair)
        processed_pairs: set[tuple[str, str]] = set()
        # Accumulate per-pair resolutions before finalizing
        pair_resolutions: dict[tuple[str, str], list[RuleApplication]] = {}

        # Process diffs in severity order: HIGH first
        ordered_diffs = (
            [d for d in report.diffs if d.severity == DiffSeverity.HIGH] +
            [d for d in report.diffs if d.severity == DiffSeverity.MEDIUM] +
            [d for d in report.diffs if d.severity == DiffSeverity.LOW]
        )

        for diff in ordered_diffs:
            rule_app = self._apply_rule(diff, result)
            if rule_app:
                result.rule_log.append(rule_app)
                pair_key = (diff.claim_alpha_id, diff.claim_beta_id or "")
                pair_resolutions.setdefault(pair_key, []).append(rule_app)

        # Finalize: produce one resolved claim per pair
        self._finalize_pairs(pair_resolutions, result)

        log.info(
            f"Adjudication complete: {len(result.resolved_claims)} resolved, "
            f"{result.branch_count} branches, "
            f"{len(result.formal_errors)} formal errors"
        )
        return result

    # ── Rule dispatcher ───────────────────────────────────────────────────────

    def _apply_rule(
        self,
        diff:   DiffNode,
        result: AdjudicationResult,
    ) -> Optional[RuleApplication]:
        """Dispatch diff to the correct rule handler."""

        dt = diff.diff_type

        # C.8 — Category (structural, highest priority)
        if dt == DiffType.CATEGORY_MISMATCH:
            return self._rule_C8_category(diff, result)

        # C.9 — Assumptions
        if dt in (DiffType.ASSUMPTION_MISMATCH, DiffType.ASSUMPTION_SCOPE_MISMATCH):
            return self._rule_C9_assumptions(diff, result)

        # C.7 — Relation type (causal scale priority)
        if dt in (DiffType.RELATION_MISMATCH, DiffType.CAUSALITY_MISMATCH):
            return self._rule_C7_relation(diff, result)

        # C.6 — Uncertainty (non-aggregative)
        if dt in (
            DiffType.UNCERTAINTY_DIVERGENCE_MINOR,
            DiffType.UNCERTAINTY_DIVERGENCE_MAJOR,
            DiffType.UNCERTAINTY_TYPE_MISMATCH,
        ):
            return self._rule_C6_uncertainty(diff, result)

        # C.1 — Modality
        if dt == DiffType.MODALITY_MISMATCH:
            return self._rule_C1_modality(diff, result)

        # C.4 — Evidence strength
        if dt == DiffType.EVIDENCE_STRENGTH_MISMATCH:
            return self._rule_C4_evidence(diff, result)

        # C.5 — Source links
        if dt in (DiffType.SOURCE_LINK_MISMATCH, DiffType.CITATION_SCOPE_MISMATCH):
            return self._rule_C5_sources(diff, result)

        # C.2 — Missing claim
        if dt == DiffType.MISSING_CLAIM:
            return self._rule_C2_missing(diff, result)

        # C.3 — Remaining diffs (combined/advisory)
        return self._rule_C3_combined(diff, result)

    # ── Rule C.8 — Category ───────────────────────────────────────────────────

    def _rule_C8_category(
        self, diff: DiffNode, result: AdjudicationResult
    ) -> RuleApplication:
        """
        C.8: Category mismatch = FORMAL_ERROR. No compromise.
        EMPIRICAL vs MODEL: keep both as separate claims.
        EMPIRICAL vs NORMATIVE: structural violation — flag both.
        """
        ca = self._alpha.get(diff.claim_alpha_id)
        cb = self._beta.get(diff.claim_beta_id or "")

        if ca is None or cb is None:
            diff.resolve("C.8-missing-node")
            return RuleApplication(
                rule="C.8", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.FORMAL_ERROR,
                reason="C.8: One or both nodes missing.",
            )

        # EMPIRICAL vs NORMATIVE — hard structural violation
        problematic = {Category.EMPIRICAL, Category.NORMATIVE}
        if {ca.category, cb.category} == problematic:
            result.formal_errors.append(diff)
            diff.resolve("C.8-formal-error")

            j = JudgmentNode.new(
                diff_type     = diff.diff_type.value,
                claim_alpha_id = diff.claim_alpha_id,
                claim_beta_id  = diff.claim_beta_id or "",
                outcome        = AdjudicationOutcome.FORMAL_ERROR.value,
                reason         = (
                    "C.8: EMPIRICAL vs NORMATIVE category mismatch. "
                    "This is a structural violation — no compromise possible. "
                    "Both claims preserved as FORMAL_ERROR for audit."
                ),
                rule_applied   = "C.8",
            )
            result.judgments.append(j)

            # Mark both claims as FORMAL_ERROR
            ca_err = copy.deepcopy(ca)
            ca_err.status = EpistemicStatus.FORMAL_ERROR
            cb_err = copy.deepcopy(cb)
            cb_err.status = EpistemicStatus.FORMAL_ERROR
            result.resolved_claims.extend([ca_err, cb_err])

            return RuleApplication(
                rule="C.8", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.FORMAL_ERROR,
                reason="EMPIRICAL vs NORMATIVE — structural violation.",
            )

        # EMPIRICAL vs MODEL or EMPIRICAL vs SPECULATIVE —
        # keep both as separate branches with STABLE_AMBIGUITY
        ca_branch = copy.deepcopy(ca)
        ca_branch.status = EpistemicStatus.STABLE_AMBIGUITY
        cb_branch = copy.deepcopy(cb)
        cb_branch.status = EpistemicStatus.STABLE_AMBIGUITY
        branch = BranchNode.new(
            trigger_diff_ids = [diff.diff_id],
            branch_reason    = (
                f"C.8: {ca.category.value} vs {cb.category.value} — "
                "incompatible epistemic framings. Neither suppressed."
            ),
            claim_alpha_id   = ca.claim_id,
            claim_beta_id    = cb.claim_id,
            merge_policy     = MergePolicy.ON_SCOPE_CHANGE,
        )
        result.branch_triggers.append(branch)
        diff.resolve("C.8-branch", branch=True)

        j = JudgmentNode.new(
            diff_type      = diff.diff_type.value,
            claim_alpha_id = diff.claim_alpha_id,
            claim_beta_id  = diff.claim_beta_id or "",
            outcome        = AdjudicationOutcome.STABLE_AMBIGUITY.value,
            reason         = (
                f"C.8: {ca.category.value} vs {cb.category.value} — "
                "both are legitimate but incompatible epistemic framings. "
                "Branch triggered. Neither suppressed."
            ),
            rule_applied   = "C.8",
        )
        result.judgments.append(j)

        return RuleApplication(
            rule="C.8", diff_id=diff.diff_id,
            outcome=AdjudicationOutcome.STABLE_AMBIGUITY,
            reason=f"{ca.category.value} vs {cb.category.value} — branch.",
        )

    # ── Rule C.9 — Assumptions ────────────────────────────────────────────────

    def _rule_C9_assumptions(
        self, diff: DiffNode, result: AdjudicationResult
    ) -> RuleApplication:
        """
        C.9: Assumption mismatch check.
        If claims differ only in assumptions (not content), this is
        assumption_separation — they can coexist as separate claims,
        not a CONTRADICTS.
        If assumptions are structurally incompatible, flag for human review.
        """
        ca = self._alpha.get(diff.claim_alpha_id)
        cb = self._beta.get(diff.claim_beta_id or "")

        if ca is None or cb is None:
            diff.resolve("C.9-missing")
            return RuleApplication(
                rule="C.9", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.FORMAL_ERROR,
                reason="C.9: Node missing.",
            )

        # Assumption-scope mismatch: different scope conditions
        # → separate the claims, keep both as VALIDATED under their own assumptions
        if diff.diff_type == DiffType.ASSUMPTION_SCOPE_MISMATCH:
            # Merge: keep alpha's assumptions as primary, beta's as alternative scope
            merged = copy.deepcopy(ca)
            merged.assumptions = list(set(ca.assumptions) | set(cb.assumptions))
            merged.builder_origin = BuilderOrigin.ADJUDICATED
            merged.status = EpistemicStatus.UNVALIDATED
            merged.lineage = [ca.claim_id, cb.claim_id]

            diff.resolve("C.9-scope-merge")
            j = JudgmentNode.new(
                diff_type      = diff.diff_type.value,
                claim_alpha_id = diff.claim_alpha_id,
                claim_beta_id  = diff.claim_beta_id or "",
                outcome        = AdjudicationOutcome.REFINEMENT.value,
                reason         = (
                    "C.9: Assumption-scope mismatch. "
                    "Claims are compatible under broader assumption set. "
                    "Merged assumptions list adopted."
                ),
                rule_applied   = "C.9",
            )
            j.winning_id = merged.claim_id
            result.judgments.append(j)

            return RuleApplication(
                rule="C.9", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.REFINEMENT,
                reason="Assumption scope merge.",
                winning_id=merged.claim_id,
            )

        # Content assumption mismatch: genuinely different assumptions
        # → STABLE_AMBIGUITY — cannot resolve without domain knowledge
        ca_b = copy.deepcopy(ca)
        ca_b.status = EpistemicStatus.STABLE_AMBIGUITY
        cb_b = copy.deepcopy(cb)
        cb_b.status = EpistemicStatus.STABLE_AMBIGUITY
        branch = BranchNode.new(
            trigger_diff_ids = [diff.diff_id],
            branch_reason    = (
                "C.9: Content assumption mismatch — claims valid under "
                "different assumption sets. Not a CONTRADICTS."
            ),
            claim_alpha_id   = ca.claim_id,
            claim_beta_id    = cb.claim_id,
            merge_policy     = MergePolicy.ON_NEW_EVIDENCE,
        )
        result.branch_triggers.append(branch)
        diff.resolve("C.9-stable-ambiguity", branch=True)

        j = JudgmentNode.new(
            diff_type      = diff.diff_type.value,
            claim_alpha_id = diff.claim_alpha_id,
            claim_beta_id  = diff.claim_beta_id or "",
            outcome        = AdjudicationOutcome.STABLE_AMBIGUITY.value,
            reason         = (
                "C.9: Content assumption mismatch — claims valid under "
                "different assumption sets. STABLE_AMBIGUITY. "
                "Note: this is NOT a CONTRADICTS — different assumptions, "
                "not incompatible truths (Annex F.3)."
            ),
            rule_applied   = "C.9",
        )
        result.judgments.append(j)

        return RuleApplication(
            rule="C.9", diff_id=diff.diff_id,
            outcome=AdjudicationOutcome.STABLE_AMBIGUITY,
            reason="Assumption content mismatch — branch.",
        )

    # ── Rule C.7 — Relation type ──────────────────────────────────────────────

    def _rule_C7_relation(
        self, diff: DiffNode, result: AdjudicationResult
    ) -> RuleApplication:
        """
        C.7: Defensive priority — lower causal scale priority wins.
        Exception: category_mismatch goes before C.7 (handled by C.8).
        CAUSALITY_MISMATCH (gap ≥ 3): trigger STABLE_AMBIGUITY.
        """
        ca = self._alpha.get(diff.claim_alpha_id)
        cb = self._beta.get(diff.claim_beta_id or "")

        if ca is None or cb is None:
            diff.resolve("C.7-missing")
            return RuleApplication(
                rule="C.7", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.FORMAL_ERROR,
                reason="C.7: Node missing.",
            )

        prio_a = causal_priority(ca.predicate)
        prio_b = causal_priority(cb.predicate)

        # CAUSALITY_MISMATCH: fundamental disagreement on causal vs correlational
        if diff.diff_type == DiffType.CAUSALITY_MISMATCH:
            ca_b = copy.deepcopy(ca)
            ca_b.status = EpistemicStatus.STABLE_AMBIGUITY
            cb_b = copy.deepcopy(cb)
            cb_b.status = EpistemicStatus.STABLE_AMBIGUITY
            branch = BranchNode.new(
                trigger_diff_ids = [diff.diff_id],
                branch_reason    = (
                    f"C.7: Causality mismatch — "
                    f"Alpha={ca.predicate!r} (prio={causal_priority(ca.predicate)}), "
                    f"Beta={cb.predicate!r} (prio={causal_priority(cb.predicate)}). "
                    "Gap ≥ 3 on causal scale: one reads causal, other correlational."
                ),
                claim_alpha_id   = ca.claim_id,
                claim_beta_id    = cb.claim_id,
                merge_policy     = MergePolicy.ON_NEW_EVIDENCE,
            )
            result.branch_triggers.append(branch)
            diff.resolve("C.7-causality-branch", branch=True)

            j = JudgmentNode.new(
                diff_type      = diff.diff_type.value,
                claim_alpha_id = diff.claim_alpha_id,
                claim_beta_id  = diff.claim_beta_id or "",
                outcome        = AdjudicationOutcome.STABLE_AMBIGUITY.value,
                reason         = (
                    f"C.7: Causality mismatch — "
                    f"Alpha={ca.predicate!r} (prio={prio_a}), "
                    f"Beta={cb.predicate!r} (prio={prio_b}). "
                    "Gap ≥ 3 on causal scale: one reads causal, other correlational. "
                    "STABLE_AMBIGUITY triggered. Neither reading suppressed."
                ),
                rule_applied   = "C.7",
            )
            result.judgments.append(j)

            return RuleApplication(
                rule="C.7", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.STABLE_AMBIGUITY,
                reason=f"Causality mismatch {ca.predicate} vs {cb.predicate}.",
            )

        # RELATION_MISMATCH: defensive priority wins (lower index on causal scale)
        if prio_a <= prio_b:
            winner, loser = ca, cb
        else:
            winner, loser = cb, ca

        resolved = copy.deepcopy(winner)
        resolved.builder_origin = BuilderOrigin.ADJUDICATED
        resolved.status = EpistemicStatus.UNVALIDATED
        resolved.lineage = [ca.claim_id, cb.claim_id]
        # Preserve larger sigma (non-aggregation)
        if ca.uncertainty and cb.uncertainty:
            if cb.uncertainty.sigma > ca.uncertainty.sigma:
                resolved.uncertainty = copy.deepcopy(cb.uncertainty)
        diff.resolve("C.7-convergence")

        j = JudgmentNode.new(
            diff_type      = diff.diff_type.value,
            claim_alpha_id = diff.claim_alpha_id,
            claim_beta_id  = diff.claim_beta_id or "",
            outcome        = AdjudicationOutcome.CONVERGENCE.value,
            reason         = (
                f"C.7: Defensive priority — "
                f"winner={winner.predicate!r} (prio={causal_priority(winner.predicate)}), "
                f"discarded={loser.predicate!r} (prio={causal_priority(loser.predicate)}). "
                "More conservative relation adopted."
            ),
            rule_applied   = "C.7",
        )
        j.winning_id = winner.claim_id
        result.judgments.append(j)

        return RuleApplication(
            rule="C.7", diff_id=diff.diff_id,
            outcome=AdjudicationOutcome.CONVERGENCE,
            reason=f"Defensive: {winner.predicate} wins over {loser.predicate}.",
            winning_id=winner.claim_id,
        )

    # ── Rule C.6 — Uncertainty ────────────────────────────────────────────────

    def _rule_C6_uncertainty(
        self, diff: DiffNode, result: AdjudicationResult
    ) -> RuleApplication:
        """
        C.6: Non-aggregative uncertainty.
        No averaging. Larger sigma preserved.
        Both original uncertainty tuples kept in Judgment record.
        """
        ca = self._alpha.get(diff.claim_alpha_id)
        cb = self._beta.get(diff.claim_beta_id or "")

        if ca is None or cb is None:
            diff.resolve("C.6-missing")
            return RuleApplication(
                rule="C.6", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.FORMAL_ERROR,
                reason="C.6: Node missing.",
            )

        # UNCERTAINTY_TYPE_MISMATCH: one has uncertainty, other doesn't
        if diff.diff_type == DiffType.UNCERTAINTY_TYPE_MISMATCH:
            # Conservative: adopt the one with uncertainty
            winner = ca if ca.uncertainty else cb
            resolved = copy.deepcopy(winner)
            resolved.builder_origin = BuilderOrigin.ADJUDICATED
            resolved.lineage = [ca.claim_id, cb.claim_id]
            diff.resolve("C.6-type-convergence")

            j = JudgmentNode.new(
                diff_type      = diff.diff_type.value,
                claim_alpha_id = diff.claim_alpha_id,
                claim_beta_id  = diff.claim_beta_id or "",
                outcome        = AdjudicationOutcome.CONVERGENCE.value,
                reason         = (
                    "C.6: Uncertainty type mismatch. "
                    "Claim with explicit uncertainty adopted (more conservative). "
                    "Non-aggregation rule: no averaging (Section VII.4)."
                ),
                rule_applied   = "C.6",
            )
            j.winning_id = winner.claim_id
            result.judgments.append(j)

            return RuleApplication(
                rule="C.6", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.CONVERGENCE,
                reason="Type mismatch: uncertainty-bearing claim adopted.",
                winning_id=winner.claim_id,
            )

        # Sigma divergence — preserve larger sigma (epistemic caution)
        ua = ca.uncertainty
        ub = cb.uncertainty

        if ua is None or ub is None:
            diff.resolve("C.6-no-uncertainty")
            return RuleApplication(
                rule="C.6", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.CONVERGENCE,
                reason="C.6: One or both uncertainty tuples absent — skipped.",
            )

        larger_sigma = ua if ua.sigma >= ub.sigma else ub
        winner = ca if ua.sigma >= ub.sigma else cb

        resolved = copy.deepcopy(winner)
        resolved.uncertainty = copy.deepcopy(larger_sigma)
        resolved.builder_origin = BuilderOrigin.ADJUDICATED
        resolved.lineage = [ca.claim_id, cb.claim_id]
        diff.resolve("C.6-sigma-convergence")

        j = JudgmentNode.new(
            diff_type      = diff.diff_type.value,
            claim_alpha_id = diff.claim_alpha_id,
            claim_beta_id  = diff.claim_beta_id or "",
            outcome        = AdjudicationOutcome.CONVERGENCE.value,
            reason         = (
                f"C.6: Non-aggregative uncertainty. "
                f"Alpha: σ={ua.sigma} CI={ua.ci} n={ua.n}. "
                f"Beta: σ={ub.sigma} CI={ub.ci} n={ub.n}. "
                f"Larger sigma preserved: σ={larger_sigma.sigma}. "
                "No averaging (Section VII.4)."
            ),
            rule_applied   = "C.6",
        )
        j.winning_id = winner.claim_id
        result.judgments.append(j)

        return RuleApplication(
            rule="C.6", diff_id=diff.diff_id,
            outcome=AdjudicationOutcome.CONVERGENCE,
            reason=f"Larger sigma σ={larger_sigma.sigma} preserved.",
            winning_id=winner.claim_id,
        )

    # ── Rule C.1 — Modality ───────────────────────────────────────────────────

    def _rule_C1_modality(
        self, diff: DiffNode, result: AdjudicationResult
    ) -> RuleApplication:
        """C.1: Weaker modality wins (more conservative)."""
        ca = self._alpha.get(diff.claim_alpha_id)
        cb = self._beta.get(diff.claim_beta_id or "")

        if ca is None or cb is None:
            diff.resolve("C.1-missing")
            return RuleApplication(
                rule="C.1", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.FORMAL_ERROR,
                reason="C.1: Node missing.",
            )

        modality_strength = {
            Modality.HYPOTHESIS:  0,
            Modality.SUGGESTION:  1,
            Modality.EVIDENCE:    2,
            Modality.ESTABLISHED: 3,
        }

        strength_a = modality_strength.get(ca.modality, 0)
        strength_b = modality_strength.get(cb.modality, 0)
        winner = ca if strength_a <= strength_b else cb

        resolved = copy.deepcopy(winner)
        resolved.builder_origin = BuilderOrigin.ADJUDICATED
        resolved.lineage = [ca.claim_id, cb.claim_id]
        diff.resolve("C.1-convergence")

        j = JudgmentNode.new(
            diff_type      = diff.diff_type.value,
            claim_alpha_id = diff.claim_alpha_id,
            claim_beta_id  = diff.claim_beta_id or "",
            outcome        = AdjudicationOutcome.CONVERGENCE.value,
            reason         = (
                f"C.1: Weaker modality wins. "
                f"Alpha={ca.modality.value} (strength={strength_a}), "
                f"Beta={cb.modality.value} (strength={strength_b}). "
                f"Adopted: {winner.modality.value}."
            ),
            rule_applied   = "C.1",
        )
        j.winning_id = winner.claim_id
        result.judgments.append(j)

        return RuleApplication(
            rule="C.1", diff_id=diff.diff_id,
            outcome=AdjudicationOutcome.CONVERGENCE,
            reason=f"Weaker modality {winner.modality.value} adopted.",
            winning_id=winner.claim_id,
        )

    # ── Rule C.4 — Evidence strength ─────────────────────────────────────────

    def _rule_C4_evidence(
        self, diff: DiffNode, result: AdjudicationResult
    ) -> RuleApplication:
        """C.4: More conservative evidence assessment wins."""
        ca = self._alpha.get(diff.claim_alpha_id)
        cb = self._beta.get(diff.claim_beta_id or "")

        if ca is None or cb is None:
            diff.resolve("C.4-missing")
            return RuleApplication(
                rule="C.4", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.FORMAL_ERROR,
                reason="C.4: Node missing.",
            )

        # Weaker evidence + larger uncertainty = more conservative
        winner = ca
        if cb.uncertainty and ca.uncertainty:
            winner = ca if ca.uncertainty.sigma >= cb.uncertainty.sigma else cb
        elif cb.uncertainty:
            winner = cb

        resolved = copy.deepcopy(winner)
        resolved.builder_origin = BuilderOrigin.ADJUDICATED
        resolved.lineage = [ca.claim_id, cb.claim_id]
        diff.resolve("C.4-convergence")

        j = JudgmentNode.new(
            diff_type      = diff.diff_type.value,
            claim_alpha_id = diff.claim_alpha_id,
            claim_beta_id  = diff.claim_beta_id or "",
            outcome        = AdjudicationOutcome.CONVERGENCE.value,
            reason         = "C.4: More conservative evidence assessment adopted.",
            rule_applied   = "C.4",
        )
        j.winning_id = winner.claim_id
        result.judgments.append(j)

        return RuleApplication(
            rule="C.4", diff_id=diff.diff_id,
            outcome=AdjudicationOutcome.CONVERGENCE,
            reason="Conservative evidence adopted.",
            winning_id=winner.claim_id,
        )

    # ── Rule C.5 — Source links ───────────────────────────────────────────────

    def _rule_C5_sources(
        self, diff: DiffNode, result: AdjudicationResult
    ) -> RuleApplication:
        """C.5: Union of sources — both sets preserved."""
        ca = self._alpha.get(diff.claim_alpha_id)
        cb = self._beta.get(diff.claim_beta_id or "")

        if ca is None or cb is None:
            diff.resolve("C.5-missing")
            return RuleApplication(
                rule="C.5", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.FORMAL_ERROR,
                reason="C.5: Node missing.",
            )

        resolved = copy.deepcopy(ca)
        resolved.source_refs   = sorted(set(ca.source_refs)   | set(cb.source_refs))
        resolved.evidence_refs = sorted(set(ca.evidence_refs) | set(cb.evidence_refs))
        resolved.builder_origin = BuilderOrigin.ADJUDICATED
        resolved.lineage = [ca.claim_id, cb.claim_id]
        diff.resolve("C.5-union")

        j = JudgmentNode.new(
            diff_type      = diff.diff_type.value,
            claim_alpha_id = diff.claim_alpha_id,
            claim_beta_id  = diff.claim_beta_id or "",
            outcome        = AdjudicationOutcome.REFINEMENT.value,
            reason         = (
                f"C.5: Source union. "
                f"Combined {len(resolved.source_refs)} source refs."
            ),
            rule_applied   = "C.5",
        )
        j.winning_id = resolved.claim_id
        result.judgments.append(j)

        return RuleApplication(
            rule="C.5", diff_id=diff.diff_id,
            outcome=AdjudicationOutcome.REFINEMENT,
            reason=f"Sources merged: {len(resolved.source_refs)} total.",
            winning_id=resolved.claim_id,
        )

    # ── Rule C.2 — Missing claim ──────────────────────────────────────────────

    def _rule_C2_missing(
        self, diff: DiffNode, result: AdjudicationResult
    ) -> RuleApplication:
        """
        C.2: Conservative inclusion.
        A claim found by one builder but not the other is kept as UNVALIDATED.
        It is not discarded — absence in one builder may be an extraction error.
        """
        # Determine which claim we have
        claim = (
            self._alpha.get(diff.claim_alpha_id) or
            self._beta.get(diff.claim_alpha_id)   # beta id stored in alpha field for beta-only
        )

        if claim is None:
            diff.resolve("C.2-missing-node")
            return RuleApplication(
                rule="C.2", diff_id=diff.diff_id,
                outcome=AdjudicationOutcome.FORMAL_ERROR,
                reason="C.2: Cannot locate claim node.",
            )

        # Keep as UNVALIDATED — needs cross-validation before sealing
        preserved = copy.deepcopy(claim)
        preserved.status = EpistemicStatus.UNVALIDATED
        result.resolved_claims.append(preserved)
        diff.resolve("C.2-preserved")

        j = JudgmentNode.new(
            diff_type      = diff.diff_type.value,
            claim_alpha_id = diff.claim_alpha_id,
            claim_beta_id  = diff.claim_beta_id or "",
            outcome        = AdjudicationOutcome.CONVERGENCE.value,
            reason         = (
                "C.2: Conservative inclusion. "
                "Claim found by one builder preserved as UNVALIDATED. "
                "Absence in other builder treated as possible extraction gap, "
                "not evidence of non-existence."
            ),
            rule_applied   = "C.2",
        )
        j.winning_id = preserved.claim_id
        result.judgments.append(j)

        return RuleApplication(
            rule="C.2", diff_id=diff.diff_id,
            outcome=AdjudicationOutcome.CONVERGENCE,
            reason="Conservative inclusion as UNVALIDATED.",
            winning_id=preserved.claim_id,
        )

    # ── Rule C.3 — Combined / advisory ───────────────────────────────────────

    def _rule_C3_combined(
        self, diff: DiffNode, result: AdjudicationResult
    ) -> RuleApplication:
        """
        C.3: Catch-all for diffs not covered by C.1–C.9.

        Protocol invariant (Section V-A, Annex C):
            No implicit resolution. No silent winner.
            An unrecognised diff type MUST surface as UNRESOLVED_PENDING_RULE
            so that the gap in the rulebook becomes visible and auditable.

        Outcome: STABLE_AMBIGUITY
            Both claims preserved. A JudgmentNode records the missing rule.
            The diff is NOT marked resolved — it remains OPEN until an
            explicit rule is added to the rulebook and re-adjudication runs.

        This is intentionally conservative. A claim that cannot be adjudicated
        by an explicit rule must not be silently decided by builder order.
        """
        ca = self._alpha.get(diff.claim_alpha_id)
        cb = self._beta.get(diff.claim_beta_id or "")

        # Preserve both claims as STABLE_AMBIGUITY — no winner
        if ca:
            ca_b = copy.deepcopy(ca)
            ca_b.status = EpistemicStatus.STABLE_AMBIGUITY
        if cb:
            cb_b = copy.deepcopy(cb)
            cb_b.status = EpistemicStatus.STABLE_AMBIGUITY

        if ca and cb:
            branch = BranchNode.new(
                trigger_diff_ids = [diff.diff_id],
                branch_reason    = (
                    f"C.3: UNRESOLVED_PENDING_RULE for {diff.diff_type.value}. "
                    "No explicit rule exists. Branch preserved pending rule extension."
                ),
                claim_alpha_id   = ca.claim_id,
                claim_beta_id    = cb.claim_id,
                merge_policy     = MergePolicy.ON_RULE_EXTENSION,
            )
            result.branch_triggers.append(branch)
        elif ca:
            result.resolved_claims.append(ca_b)
        elif cb:
            result.resolved_claims.append(cb_b)

        # Do NOT mark diff as resolved — leave OPEN for re-adjudication
        # diff.resolve("C.3-...") intentionally omitted

        log.warning(
            f"C.3: No rule matched diff_type={diff.diff_type.value} "
            f"diff={diff.diff_id[:8]}… — UNRESOLVED_PENDING_RULE. "
            "Add an explicit rule to the Adjudication Rulebook."
        )

        j = JudgmentNode.new(
            diff_type      = diff.diff_type.value,
            claim_alpha_id = diff.claim_alpha_id,
            claim_beta_id  = diff.claim_beta_id or "",
            outcome        = AdjudicationOutcome.STABLE_AMBIGUITY.value,
            reason         = (
                f"C.3: UNRESOLVED_PENDING_RULE for diff_type={diff.diff_type.value}. "
                "No explicit adjudication rule exists for this diff type. "
                "No silent winner assigned. Both claims preserved as STABLE_AMBIGUITY. "
                "Diff remains OPEN. Protocol requires an explicit rule to be added "
                "before this diff can be resolved. (Section V-A invariant: "
                "no implicit resolution, no verdeckter Default.)"
            ),
            rule_applied   = "C.3",
        )
        result.judgments.append(j)

        return RuleApplication(
            rule="C.3", diff_id=diff.diff_id,
            outcome=AdjudicationOutcome.STABLE_AMBIGUITY,
            reason=f"UNRESOLVED_PENDING_RULE: {diff.diff_type.value} — no winner assigned.",
            winning_id=None,  # explicitly None — no default winner
        )

    # ── Finalize pairs ────────────────────────────────────────────────────────

    def _finalize_pairs(
        self,
        pair_resolutions: dict[tuple[str, str], list[RuleApplication]],
        result: AdjudicationResult,
    ):
        """
        After all rules have been applied, produce one final resolved ClaimNode
        per Alpha/Beta pair — incorporating all rule decisions.
        """
        processed_winners: set[str] = set()

        for (alpha_id, beta_id), applications in pair_resolutions.items():
            # Find the most significant outcome for this pair
            has_branch = any(
                a.outcome in (
                    AdjudicationOutcome.STABLE_AMBIGUITY,
                    AdjudicationOutcome.FORMAL_ERROR,
                )
                for a in applications
            )

            if has_branch:
                # Branch already added to result.branch_triggers in rule handlers
                continue

            # Find the winning claim from the applications
            # Highest-priority rule wins (C.8 > C.9 > C.7 > ...)
            rule_priority = ["C.8","C.9","C.7","C.6","C.1","C.4","C.5","C.2","C.3"]
            winning_id = None
            for rule in rule_priority:
                for app in applications:
                    if app.rule == rule and app.winning_id:
                        winning_id = app.winning_id
                        break
                if winning_id:
                    break

            if winning_id and winning_id not in processed_winners:
                claim = (
                    self._alpha.get(winning_id) or
                    self._beta.get(winning_id)
                )
                if claim:
                    resolved = copy.deepcopy(claim)
                    resolved.builder_origin = BuilderOrigin.ADJUDICATED
                    resolved.status = EpistemicStatus.UNVALIDATED
                    if alpha_id not in resolved.lineage:
                        resolved.lineage.append(alpha_id)
                    if beta_id and beta_id not in resolved.lineage:
                        resolved.lineage.append(beta_id)
                    result.resolved_claims.append(resolved)
                    processed_winners.add(winning_id)
