"""
Alexandria Core — Relations Admissibility Matrix
=================================================

Sprint 2 (v2.2) — Annex F operational enforcement layer.

This module operationalizes the Relationsontologie (Annex F) as a
machine-checkable admissibility matrix. It answers three questions
for any (category, predicate) combination:

    1. Is this predicate ALLOWED for this category?
    2. Is uncertainty REQUIRED?
    3. Does a conflict on this predicate trigger a Branch?

Protocol invariant [SHALL]:
    NORMATIVE claims MUST NOT use causal/empirical predicates.
    SPECULATIVE claims MUST NOT use CAUSES.
    MODEL claims MUST carry explicit model assumptions when using strong predicates.

Reference: Annex F (v2.1), Section III.3, Section V-A (DBA)

Usage
-----
>>> from alexandria_core.relations import RelationsMatrix, AdmissibilityResult
>>> result = RelationsMatrix.check(category, predicate)
>>> if not result.allowed:
...     raise ValueError(result.reason)
>>> if result.uncertainty_required:
...     assert claim.uncertainty is not None
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from .schema import Category, RelationType


# ── Predicate groupings ───────────────────────────────────────────────────────

# Causal scale (Annex F.2) — ordered weakest → strongest
CAUSAL_SCALE: list[str] = [
    "MENTIONS",
    "RELATES_TO",
    "CORRELATES_WITH",
    "PARTIALLY_SUPPORTS",
    "SUPPORTS",
    "STRONGLY_SUPPORTS",
    "CONTRIBUTES_TO",
    "CAUSES",
]

# Predicates that carry causal/statistical semantics
CAUSAL_PREDICATES: frozenset[str] = frozenset({
    "CORRELATES_WITH",
    "PARTIALLY_SUPPORTS",
    "SUPPORTS",
    "STRONGLY_SUPPORTS",
    "CONTRIBUTES_TO",
    "CAUSES",
})

# Predicates that imply hard causal claims
HARD_CAUSAL_PREDICATES: frozenset[str] = frozenset({
    "CAUSES",
    "CONTRIBUTES_TO",
    "STRONGLY_SUPPORTS",
})

# Predicates that are structural / relational (no causal semantics)
STRUCTURAL_PREDICATES: frozenset[str] = frozenset({
    "MENTIONS",
    "RELATES_TO",
    "INSTANCE_OF",
    "SUBCLASS_OF",
    "PART_OF",
    "AUTHORED_BY",
    "CITES",
    "PATCH_ANCHORS_TO",
    "CONTRADICTS",
    "PARTIALLY_CONTRADICTS",
    "REFINES",
    "GENERALIZES",
    "DERIVED_FROM",
    "REPLACES",
    "EXTENDS",
    "HAS_EVIDENCE",
    "SUPPORTED_BY",
    "MENTIONED_IN",
    "CONTRADICTED_BY",
    "REPLICATED_BY",
})


# ── Admissibility result ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class AdmissibilityResult:
    """
    Result of checking a (category, predicate) combination.

    allowed              — is this predicate permitted for this category?
    uncertainty_required — must the claim carry an uncertainty tuple?
    branch_on_conflict   — if Alpha/Beta disagree on this predicate → Branch?
    reason               — human-readable explanation
    rule_ref             — Annex F section reference
    """
    allowed:              bool
    uncertainty_required: bool
    branch_on_conflict:   bool
    reason:               str
    rule_ref:             str


# ── The matrix ────────────────────────────────────────────────────────────────

# Structure: category → predicate → AdmissibilityResult
# Predicates not listed for a category fall through to the DEFAULT rule.

_MATRIX: dict[str, dict[str, AdmissibilityResult]] = {

    # ── EMPIRICAL ────────────────────────────────────────────────────────────
    "EMPIRICAL": {
        # Full causal scale allowed; uncertainty required for evidence/established
        # (checked dynamically by EpistemicIdentity.uncertainty_required)
        "CAUSES": AdmissibilityResult(
            allowed=True, uncertainty_required=True, branch_on_conflict=True,
            reason="EMPIRICAL/CAUSES: allowed but requires maximal evidence (sigma, ci, n). "
                   "Gap ≥ 3 vs any weaker predicate triggers STABLE_AMBIGUITY.",
            rule_ref="Annex F.2, F.6",
        ),
        "CONTRIBUTES_TO": AdmissibilityResult(
            allowed=True, uncertainty_required=True, branch_on_conflict=True,
            reason="EMPIRICAL/CONTRIBUTES_TO: allowed; other contributing factors must be "
                   "declared in assumptions[].",
            rule_ref="Annex F.2",
        ),
        "STRONGLY_SUPPORTS": AdmissibilityResult(
            allowed=True, uncertainty_required=True, branch_on_conflict=True,
            reason="EMPIRICAL/STRONGLY_SUPPORTS: allowed with replicated evidence.",
            rule_ref="Annex F.2",
        ),
        "SUPPORTS": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=False,
            reason="EMPIRICAL/SUPPORTS: allowed; uncertainty recommended but not required.",
            rule_ref="Annex F.2",
        ),
        "CORRELATES_WITH": AdmissibilityResult(
            allowed=True, uncertainty_required=True, branch_on_conflict=False,
            reason="EMPIRICAL/CORRELATES_WITH: allowed; must carry {sigma, ci, n}. "
                   "Does NOT imply causation.",
            rule_ref="Annex F.2",
        ),
        "PARTIALLY_SUPPORTS": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=False,
            reason="EMPIRICAL/PARTIALLY_SUPPORTS: allowed under declared assumptions.",
            rule_ref="Annex F.2",
        ),
        "MENTIONS": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=False,
            reason="EMPIRICAL/MENTIONS: weak relation — no causal claim.",
            rule_ref="Annex F.2",
        ),
        "RELATES_TO": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=False,
            reason="EMPIRICAL/RELATES_TO: associative — no directional claim.",
            rule_ref="Annex F.2",
        ),
    },

    # ── NORMATIVE ────────────────────────────────────────────────────────────
    "NORMATIVE": {
        # No causal or statistical predicates allowed.
        # Annex F.1, Section III.3: category purity.
        "CAUSES": AdmissibilityResult(
            allowed=False, uncertainty_required=False, branch_on_conflict=True,
            reason="NORMATIVE/CAUSES: FORBIDDEN. Normative claims cannot assert empirical "
                   "causation. This is a category purity violation (FORMAL_ERROR). "
                   "Separate the empirical and normative components into distinct claims.",
            rule_ref="Annex F.1, Section III.3, Audit Block I",
        ),
        "CONTRIBUTES_TO": AdmissibilityResult(
            allowed=False, uncertainty_required=False, branch_on_conflict=True,
            reason="NORMATIVE/CONTRIBUTES_TO: FORBIDDEN. Causal predicate on normative claim.",
            rule_ref="Annex F.1",
        ),
        "STRONGLY_SUPPORTS": AdmissibilityResult(
            allowed=False, uncertainty_required=False, branch_on_conflict=True,
            reason="NORMATIVE/STRONGLY_SUPPORTS: FORBIDDEN. Empirical strength predicate "
                   "has no meaning for value claims.",
            rule_ref="Annex F.1",
        ),
        "CORRELATES_WITH": AdmissibilityResult(
            allowed=False, uncertainty_required=False, branch_on_conflict=True,
            reason="NORMATIVE/CORRELATES_WITH: FORBIDDEN. Statistical correlation predicate "
                   "cannot be applied to normative claims.",
            rule_ref="Annex F.1",
        ),
        "SUPPORTS": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=False,
            reason="NORMATIVE/SUPPORTS: allowed as normative coherence relation "
                   "(not empirical support).",
            rule_ref="Annex F.1",
        ),
        "PARTIALLY_SUPPORTS": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=False,
            reason="NORMATIVE/PARTIALLY_SUPPORTS: allowed as conditional normative coherence.",
            rule_ref="Annex F.1",
        ),
        "RELATES_TO": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=False,
            reason="NORMATIVE/RELATES_TO: allowed as generic normative connection.",
            rule_ref="Annex F.1",
        ),
        "MENTIONS": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=False,
            reason="NORMATIVE/MENTIONS: allowed.",
            rule_ref="Annex F.1",
        ),
    },

    # ── SPECULATIVE ──────────────────────────────────────────────────────────
    "SPECULATIVE": {
        "CAUSES": AdmissibilityResult(
            allowed=False, uncertainty_required=False, branch_on_conflict=True,
            reason="SPECULATIVE/CAUSES: FORBIDDEN. Speculative claims cannot assert "
                   "necessity + sufficiency. Use CONTRIBUTES_TO or SUPPORTS at most.",
            rule_ref="Annex F.1, F.2",
        ),
        "CONTRIBUTES_TO": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=True,
            reason="SPECULATIVE/CONTRIBUTES_TO: allowed under heavy assumption declaration. "
                   "Treated as hypothesis-grade causal suggestion.",
            rule_ref="Annex F.2",
        ),
        "STRONGLY_SUPPORTS": AdmissibilityResult(
            allowed=False, uncertainty_required=False, branch_on_conflict=True,
            reason="SPECULATIVE/STRONGLY_SUPPORTS: FORBIDDEN. Speculative claims cannot "
                   "assert strong replicated support — use SUPPORTS at most.",
            rule_ref="Annex F.2",
        ),
        "SUPPORTS": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=False,
            reason="SPECULATIVE/SUPPORTS: allowed as speculative support relation.",
            rule_ref="Annex F.2",
        ),
        "PARTIALLY_SUPPORTS": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=False,
            reason="SPECULATIVE/PARTIALLY_SUPPORTS: allowed.",
            rule_ref="Annex F.2",
        ),
        "CORRELATES_WITH": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=False,
            reason="SPECULATIVE/CORRELATES_WITH: allowed as speculative association "
                   "(not statistical claim).",
            rule_ref="Annex F.2",
        ),
    },

    # ── MODEL ────────────────────────────────────────────────────────────────
    "MODEL": {
        "CAUSES": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=True,
            reason="MODEL/CAUSES: allowed ONLY with explicit model derivation assumptions "
                   "in assumptions[]. Model causality ≠ empirical causality.",
            rule_ref="Annex F.2, Section III.3",
        ),
        "CONTRIBUTES_TO": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=True,
            reason="MODEL/CONTRIBUTES_TO: allowed with model assumptions declared.",
            rule_ref="Annex F.2",
        ),
        "STRONGLY_SUPPORTS": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=True,
            reason="MODEL/STRONGLY_SUPPORTS: allowed; interpreted as model-internal "
                   "strong support, not empirical replication.",
            rule_ref="Annex F.2",
        ),
        "CORRELATES_WITH": AdmissibilityResult(
            allowed=True, uncertainty_required=False, branch_on_conflict=False,
            reason="MODEL/CORRELATES_WITH: allowed as model-internal correlation.",
            rule_ref="Annex F.2",
        ),
    },
}

# Default result for (category, predicate) combinations not explicitly listed
_DEFAULT_ALLOWED = AdmissibilityResult(
    allowed=True, uncertainty_required=False, branch_on_conflict=False,
    reason="Default: predicate not restricted for this category.",
    rule_ref="Annex F (general)",
)
_DEFAULT_STRUCTURAL = AdmissibilityResult(
    allowed=True, uncertainty_required=False, branch_on_conflict=False,
    reason="Structural/ontological predicate — no category restriction.",
    rule_ref="Annex F.3–F.5",
)


# ── Public interface ──────────────────────────────────────────────────────────

class RelationsMatrix:
    """
    Machine-checkable relations admissibility matrix.

    Implements Annex F as an operational enforcement layer.
    Called by AuditGate (Block I), Adjudicator (C.8), and directly.

    [HEURISTIC]: The specific allowed/forbidden decisions in this matrix
    are DBA reference decisions. They can be overridden by implementing
    a custom matrix class that satisfies the same interface.
    """

    @staticmethod
    def check(category: Category, predicate: str) -> AdmissibilityResult:
        """
        Check whether predicate is admissible for this category.

        Args:
            category:  the claim's epistemic category
            predicate: the predicate string (RelationType.value or raw string)

        Returns:
            AdmissibilityResult with allowed, uncertainty_required, branch_on_conflict
        """
        # Structural predicates are always allowed regardless of category
        if predicate in STRUCTURAL_PREDICATES:
            return _DEFAULT_STRUCTURAL

        cat_rules = _MATRIX.get(category.value, {})
        result = cat_rules.get(predicate)
        if result is not None:
            return result

        # Not explicitly listed → default allowed
        return _DEFAULT_ALLOWED

    @staticmethod
    def validate_claim(claim: "Any") -> tuple[bool, list[str]]:
        """
        Full admissibility check for a ClaimNode.

        Returns (valid, list_of_violations).
        """
        from .schema import ClaimNode
        violations: list[str] = []

        result = RelationsMatrix.check(claim.category, claim.predicate)
        if not result.allowed:
            violations.append(
                f"[{result.rule_ref}] {result.reason}"
            )

        # NOTE: uncertainty_required check intentionally removed (Sprint 3 doctrine).
        # Uncertainty enforcement is the exclusive responsibility of
        # EpistemicIdentity.uncertainty_required(claim) in schema.py / audit.py.
        # RelationsMatrix only checks ontological admissibility (Category × Predicate).
        # Duplicating the check here would incorrectly flag EMPIRICAL/hypothesis/CAUSES
        # claims, which are legitimate even without an uncertainty tuple.

        return len(violations) == 0, violations

    @staticmethod
    def forbidden_combinations() -> list[tuple[str, str, str]]:
        """
        Return all explicitly forbidden (category, predicate, reason) triples.
        Useful for audit reporting and documentation.
        """
        result = []
        for cat, predicates in _MATRIX.items():
            for pred, admissibility in predicates.items():
                if not admissibility.allowed:
                    result.append((cat, pred, admissibility.reason))
        return result

    @staticmethod
    def requires_branch_on_conflict(category: Category, predicate: str) -> bool:
        """Returns True if Builder disagreement on this predicate must produce a Branch."""
        return RelationsMatrix.check(category, predicate).branch_on_conflict
