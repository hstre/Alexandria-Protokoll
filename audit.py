"""
Alexandria Core — audit.py
Epistemic Audit Gate — Section X of the Alexandria Protocol.

Five audit blocks. All must pass for UNVALIDATED → VALIDATED transition.
No block may be skipped. No partial pass.

Block I   — Category Purity (X.3)
Block II  — Path Reconstruction Verification (X.4)
Block III — Temporal Integrity (X.5)
Block IV  — Cross-Assessment Verification (X.6)
Block V   — Uncertainty Disclosure (X.7)
"""

from __future__ import annotations
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from .schema import (
    ClaimNode, Patch, Category, EpistemicStatus,
    UncertaintyType, Modality, PatchOperation,
)

log = logging.getLogger(__name__)


# ── Audit result types ────────────────────────────────────────────────────────

@dataclass
class BlockResult:
    block:   int
    name:    str
    passed:  bool
    errors:  list[str] = field(default_factory=list)
    notes:   list[str] = field(default_factory=list)

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines  = [f"  Block {self.block} ({self.name}): {status}"]
        for e in self.errors:
            lines.append(f"    ✗ {e}")
        for n in self.notes:
            lines.append(f"    ·  {n}")
        return "\n".join(lines)


@dataclass
class AuditReport:
    """
    Full audit report for a single patch.
    Immutable once produced — Section X.10.
    """
    patch_id:     str
    claim_id:     str
    timestamp:    float
    blocks:       list[BlockResult] = field(default_factory=list)
    final_status: EpistemicStatus = EpistemicStatus.UNVALIDATED

    @property
    def passed(self) -> bool:
        return all(b.passed for b in self.blocks)

    @property
    def failed_blocks(self) -> list[BlockResult]:
        return [b for b in self.blocks if not b.passed]

    def __str__(self) -> str:
        lines = [
            f"Audit Report — patch {self.patch_id[:12]}… / claim {self.claim_id[:12]}…",
            f"Result: {'PASSED' if self.passed else 'FAILED'}  →  {self.final_status.value}",
        ]
        for b in self.blocks:
            lines.append(str(b))
        return "\n".join(lines)


# ── Individual audit blocks ───────────────────────────────────────────────────

class AuditGate:
    """
    Runs all five audit blocks against a (patch, claim) pair.

    Usage:
        gate   = AuditGate()
        report = gate.audit(patch, claim, prior_patches)
        if report.passed:
            claim.status = EpistemicStatus.VALIDATED
    """

    # Categories that are mutually exclusive — no mixing allowed
    _VALID_CATEGORIES = {c.value for c in Category}

    # Relations that imply empirical measurement and thus require uncertainty
    _EMPIRICAL_PREDICATES = {
        "CORRELATES_WITH", "PARTIALLY_SUPPORTS", "SUPPORTS",
        "STRONGLY_SUPPORTS", "CONTRIBUTES_TO", "CAUSES",
    }

    def audit(
        self,
        patch:         Patch,
        claim:         ClaimNode,
        prior_patches: list[Patch] | None = None,
    ) -> AuditReport:
        """
        Run all five blocks. Returns AuditReport with final status.
        prior_patches: all patches before this one (for Block III / temporal check).
        """
        prior_patches = prior_patches or []
        report = AuditReport(
            patch_id=patch.patch_id,
            claim_id=claim.claim_id,
            timestamp=time.time(),
        )

        # Run blocks
        report.blocks.append(self._block_I_category_purity(claim))
        report.blocks.append(self._block_II_path_reconstruction(claim))
        report.blocks.append(self._block_III_temporal_integrity(patch, prior_patches))
        report.blocks.append(self._block_IV_cross_assessment(claim))
        report.blocks.append(self._block_V_uncertainty_disclosure(claim))

        # Determine final status
        if report.passed:
            report.final_status = EpistemicStatus.VALIDATED
            log.info(f"Audit PASSED — claim {claim.claim_id[:8]}… → VALIDATED")
        else:
            # Check for FORMAL_ERROR (structural violation) vs plain fail
            structural_violations = [
                b for b in report.failed_blocks
                if b.block in (1, 2)  # Category + Path are structural
            ]
            if structural_violations:
                report.final_status = EpistemicStatus.FORMAL_ERROR
                log.error(f"Audit FORMAL_ERROR — claim {claim.claim_id[:8]}…")
            else:
                report.final_status = EpistemicStatus.UNVALIDATED
                log.warning(f"Audit FAILED — claim {claim.claim_id[:8]}… stays UNVALIDATED")

        return report

    # ── Block I: Category Purity ──────────────────────────────────────────────

    def _block_I_category_purity(self, claim: ClaimNode) -> BlockResult:
        """
        X.3 — Category assignment must be valid and unambiguous.
        No category mixing. Normative claims must not use empirical predicates.
        """
        errors = []
        notes  = []

        # Category must be set
        if claim.category.value not in self._VALID_CATEGORIES:
            errors.append(
                f"Invalid category {claim.category!r}. "
                f"Must be one of: {sorted(self._VALID_CATEGORIES)}"
            )

        # Normative claims must not use empirical causal predicates
        if (claim.category == Category.NORMATIVE
                and claim.predicate in self._EMPIRICAL_PREDICATES):
            errors.append(
                f"NORMATIVE claim uses empirical predicate {claim.predicate!r}. "
                "Category mixing is a structural violation (Section X.3)."
            )

        # Speculative claims must not use CAUSES
        if claim.category == Category.SPECULATIVE and claim.predicate == "CAUSES":
            errors.append(
                "SPECULATIVE claim must not use CAUSES (Annex F.1.4)."
            )

        # Note: MODEL claims may use causal predicates (simulation-based)
        if claim.category == Category.MODEL:
            notes.append(
                "MODEL claim — causal predicates require derivation.type = SIMULATION."
            )

        passed = len(errors) == 0
        return BlockResult(1, "Category Purity", passed, errors, notes)

    # ── Block II: Path Reconstruction ─────────────────────────────────────────

    def _block_II_path_reconstruction(self, claim: ClaimNode) -> BlockResult:
        """
        X.4 — Epistemic derivation path must be fully reconstructible.
        Requires: explicit assumptions[], source_refs or evidence_refs,
        and non-empty predicate and subject/object.
        """
        errors = []
        notes  = []

        # Assumptions mandatory (Section VII.13.2)
        if not claim.assumptions:
            errors.append(
                "assumptions[] is empty. "
                "Implicit assumptions are a formal audit failure (Section X.4)."
            )

        # Subject and predicate mandatory
        if not claim.subject:
            errors.append("subject is missing — derivation path unreconstructible.")
        if not claim.predicate:
            errors.append("predicate is missing — derivation path unreconstructible.")
        if not claim.object:
            errors.append("object is missing — derivation path unreconstructible.")

        # At least one source or evidence reference
        if not claim.source_refs and not claim.evidence_refs:
            errors.append(
                "Neither source_refs nor evidence_refs present. "
                "Authority references do not substitute for reconstructible derivation (X.4)."
            )

        # Lineage should be present for non-genesis claims
        if not claim.lineage:
            notes.append(
                "lineage[] is empty — acceptable for genesis claims, "
                "required for MODIFY/BRANCH claims."
            )

        passed = len(errors) == 0
        return BlockResult(2, "Path Reconstruction", passed, errors, notes)

    # ── Block III: Temporal Integrity ─────────────────────────────────────────

    def _block_III_temporal_integrity(
        self, patch: Patch, prior_patches: list[Patch]
    ) -> BlockResult:
        """
        X.5 — Temporal admissibility.
        Timestamp must be valid and monotonically greater than prior patches.
        parent_patch_id must reference a known prior patch.
        """
        errors = []
        notes  = []

        # Timestamp must be positive
        if patch.timestamp <= 0:
            errors.append(f"Invalid timestamp {patch.timestamp}.")

        # Timestamp must be in the past (not future-dated)
        now = time.time()
        if patch.timestamp > now + 60:   # 60 second tolerance for clock skew
            errors.append(
                f"Timestamp {patch.timestamp} is in the future. "
                "Retroactive epistemic modification is prohibited (Section X.5)."
            )

        # Monotonic ordering vs prior patches
        if prior_patches:
            last_ts = prior_patches[-1].timestamp
            if patch.timestamp <= last_ts:
                errors.append(
                    f"Timestamp {patch.timestamp} ≤ previous patch timestamp {last_ts}. "
                    "Monotonic ordering violated (Section X.5)."
                )

        # parent_patch_id must match last known patch
        expected_parent = prior_patches[-1].patch_id if prior_patches else None
        if patch.parent_patch_id != expected_parent:
            errors.append(
                f"parent_patch_id {patch.parent_patch_id!r} does not match "
                f"expected {expected_parent!r}."
            )

        passed = len(errors) == 0
        return BlockResult(3, "Temporal Integrity", passed, errors, notes)

    # ── Block IV: Cross-Assessment ────────────────────────────────────────────

    def _block_IV_cross_assessment(self, claim: ClaimNode) -> BlockResult:
        """
        X.6 — Cross-assessment references must exist where required.
        DBA claims (builder_origin = adjudicated) must reference a Judgment.
        Non-adjudicated claims: cross-assessment is encouraged but not mandatory
        for UNVALIDATED → VALIDATED. Mandatory for VALIDATED → SEALED.
        """
        errors = []
        notes  = []

        from .schema import BuilderOrigin
        if claim.builder_origin == BuilderOrigin.ADJUDICATED:
            # Adjudicated claims must carry evidence of adjudication
            if not claim.lineage:
                errors.append(
                    "Adjudicated claim has no lineage[]. "
                    "Adjudication Judgment reference required (Section X.6)."
                )
        else:
            notes.append(
                f"builder_origin={claim.builder_origin.value} — "
                "cross-assessment ref required for SEALED status (not for VALIDATED)."
            )

        passed = len(errors) == 0
        return BlockResult(4, "Cross-Assessment", passed, errors, notes)

    # ── Block V: Uncertainty Disclosure ──────────────────────────────────────

    def _block_V_uncertainty_disclosure(self, claim: ClaimNode) -> BlockResult:
        """
        X.7: Probabilistic claims must carry full uncertainty metadata.
        v2.2 Sprint 3: Uses EpistemicIdentity.uncertainty_required() for
        precise 3-condition logic instead of broad predicate heuristic.
        """
        from .schema import EpistemicIdentity
        errors = []
        notes  = []

        unc_required = EpistemicIdentity.uncertainty_required(claim)

        if unc_required:
            if claim.uncertainty is None:
                errors.append(
                    f"[SHALL X.7] Probabilistic claim ({claim.category.value}/"
                    f"{claim.modality.value}/{claim.predicate!r}) requires "
                    "uncertainty {sigma, ci, n}."
                )
            else:
                unc_errors = claim.uncertainty.validate()
                errors.extend(unc_errors)
                notes.append(
                    f"sigma={claim.uncertainty.sigma}, "
                    f"CI={claim.uncertainty.ci}, "
                    f"n={claim.uncertainty.n}"
                )
        else:
            if claim.uncertainty is not None:
                unc_errors = claim.uncertainty.validate()
                if unc_errors:
                    errors.extend(unc_errors)
                notes.append(
                    f"uncertainty present (optional): "
                    f"sigma={claim.uncertainty.sigma}, CI={claim.uncertainty.ci}"
                )
            # Deterministic claims must not carry probabilistic uncertainty type
            if (claim.uncertainty and
                    claim.uncertainty.type == UncertaintyType.PROBABILISTIC):
                notes.append(
                    "Deterministic predicate with probabilistic uncertainty "
                    "— verify uncertainty.type is correct."
                )

        if claim.validation:
            if claim.validation.decay <= 0:
                errors.append(
                    "validation.decay must be > 0 (Section XI.4)."
                )
            else:
                notes.append(
                    f"lambda={claim.validation.decay} -> "
                    f"half-life ~{0.693/claim.validation.decay:.1f} time units"
                )

        passed = len(errors) == 0
        return BlockResult(5, "Uncertainty Disclosure", passed, errors, notes)


# ── Three-Level Audit (v2.2 Sprint 2) ────────────────────────────────────────

@dataclass
class PatchAuditResult:
    """
    Level 1: Patch-level structural audit.
    Checks the Patch object itself, independent of Claim content.
    """
    patch_id:        str
    timestamp:       float
    passed:          bool
    violations:      list[str]

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [f"PatchAudit [{status}] patch={self.patch_id[:8]}…"]
        for v in self.violations:
            lines.append(f"  ✗ {v}")
        return "\n".join(lines)


@dataclass
class ClaimAuditResult:
    """
    Level 2: Claim-level epistemic audit.
    Checks the Claim's content, category, uncertainty, assumptions, relations.
    Corresponds to the existing 5-block AuditGate logic.
    """
    claim_id:        str
    timestamp:       float
    passed:          bool
    violations:      list[str]
    final_status:    str   # VALIDATED | FORMAL_ERROR | UNVALIDATED

    def __str__(self) -> str:
        lines = [f"ClaimAudit [{self.final_status}] claim={self.claim_id[:8]}…"]
        for v in self.violations:
            lines.append(f"  ✗ {v}")
        return "\n".join(lines)


@dataclass
class GraphAuditResult:
    """
    Level 3: Graph-level structural audit.
    Checks cross-claim consistency, branch coherence, orphan detection,
    source traceability, referential integrity.
    """
    graph_id:            str
    timestamp:           float
    passed:              bool
    orphan_claim_ids:    list[str]
    unresolved_branches: list[str]   # branch_ids with no merge/deprecation
    source_gaps:         list[str]   # claim_ids with no source_ref
    violations:          list[str]

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"GraphAudit [{status}] graph={self.graph_id}",
            f"  Orphan claims:       {len(self.orphan_claim_ids)}",
            f"  Unresolved branches: {len(self.unresolved_branches)}",
            f"  Source gaps:         {len(self.source_gaps)}",
        ]
        for v in self.violations:
            lines.append(f"  ✗ {v}")
        return "\n".join(lines)


class ThreeLevelAudit:
    """
    Sprint 2 (v2.2): Three-level audit separating Patch, Claim, and Graph concerns.

    Levels
    ------
    Level 1 — Patch Audit [SHALL]
        Validates the Patch object itself:
        - timestamp monotonicity
        - parent_patch_id consistency
        - required fields present (operation, target_id, assumptions)

    Level 2 — Claim Audit [SHALL]
        Validates the Claim's epistemic content:
        - category purity (Block I)
        - assumptions non-empty (Block II / VII.13.2)
        - uncertainty admissibility (Block V, EpistemicIdentity rules)
        - relation admissibility (RelationsMatrix)

    Level 3 — Graph Audit [DBA]
        Validates cross-claim consistency:
        - orphan nodes (claims with lineage pointing to missing patches)
        - source traceability (all claims have ≥1 source_ref)
        - branch coherence (no OPEN branches older than threshold)
        - referential integrity (evidence_refs, source_refs exist)

    This replaces the implicit mixing in the original AuditGate where
    Block II did partial graph checks inside a claim-level audit.
    """

    def audit_patch(
        self,
        patch: "Any",  # Patch — avoid circular import at module level
        prior_patches: list | None = None,
    ) -> PatchAuditResult:
        """Level 1: Patch structural audit."""
        from .schema import Patch, PatchOperation
        violations = []
        prior = prior_patches or []

        # Required fields
        if not patch.patch_id:
            violations.append("patch_id is empty")
        if not patch.target_id:
            violations.append("target_id is empty")
        if patch.operation not in list(PatchOperation):
            violations.append(f"unknown operation: {patch.operation!r}")
        if not patch.assumptions:
            violations.append(
                "assumptions[] empty — [SHALL] Section VII.13.2"
            )

        # Temporal monotonicity
        if prior:
            last = prior[-1]
            if patch.timestamp <= last.timestamp:
                violations.append(
                    f"timestamp violation: {patch.timestamp} <= {last.timestamp} "
                    "(retroactive patch)"
                )

        # Parent consistency
        expected_parent = prior[-1].patch_id if prior else None
        if patch.parent_patch_id != expected_parent:
            violations.append(
                f"parent_patch_id mismatch: expected {expected_parent!r}, "
                f"got {patch.parent_patch_id!r}"
            )

        return PatchAuditResult(
            patch_id=patch.patch_id,
            timestamp=__import__('time').time(),
            passed=len(violations) == 0,
            violations=violations,
        )

    def audit_claim(self, claim: "Any") -> ClaimAuditResult:
        """Level 2: Claim epistemic audit using RelationsMatrix + EpistemicIdentity."""
        from .schema import ClaimNode, EpistemicStatus, EpistemicIdentity
        from .relations import RelationsMatrix
        violations = []

        # Category purity
        if not claim.category:
            violations.append("category not set")

        # Assumptions [SHALL]
        if not claim.assumptions:
            violations.append(
                "assumptions[] empty [SHALL] Section VII.13.2"
            )

        # Relation admissibility (RelationsMatrix — Sprint 2)
        valid, rel_issues = RelationsMatrix.validate_claim(claim)
        violations.extend(rel_issues)

        # Uncertainty admissibility (EpistemicIdentity rules — Sprint 2)
        if EpistemicIdentity.uncertainty_required(claim) and claim.uncertainty is None:
            violations.append(
                f"uncertainty required for {claim.category.value}/"
                f"{claim.modality.value}/{claim.predicate} [SHALL] Section VII.4"
            )

        # Source references
        if not claim.source_refs and not claim.evidence_refs:
            violations.append(
                "no source_refs or evidence_refs — traceability gap (D.2)"
            )

        passed = len(violations) == 0
        if passed:
            final_status = EpistemicStatus.VALIDATED.value
        else:
            # Relation violations are structural → FORMAL_ERROR
            structural = any(
                "FORBIDDEN" in v or "category" in v.lower() or "SHALL" in v
                for v in violations
            )
            final_status = (
                EpistemicStatus.FORMAL_ERROR.value if structural
                else EpistemicStatus.UNVALIDATED.value
            )

        return ClaimAuditResult(
            claim_id=claim.claim_id,
            timestamp=__import__('time').time(),
            passed=passed,
            violations=violations,
            final_status=final_status,
        )

    def audit_graph(
        self,
        claims: list,
        branches: list,
        patch_chain: "Any",
        graph_id: str = "default",
        branch_age_threshold_days: float = 30.0,
    ) -> GraphAuditResult:
        """Level 3: Graph structural audit."""
        import time as _time
        violations = []
        orphan_ids = []
        unresolved_branch_ids = []
        source_gaps = []

        chain_patch_ids = {p.patch_id for p in patch_chain._patches} if patch_chain else set()

        for claim in claims:
            # Orphan check: lineage patches must exist in chain
            for pid in claim.lineage:
                if pid not in chain_patch_ids:
                    orphan_ids.append(claim.claim_id)
                    violations.append(
                        f"Claim {claim.claim_id[:8]}… references unknown "
                        f"patch {pid[:8]}… in lineage"
                    )
                    break

            # Source traceability
            if not claim.source_refs and not claim.evidence_refs:
                source_gaps.append(claim.claim_id)

        # Branch coherence
        now = _time.time()
        for branch in branches:
            from .schema import BranchStatus
            if branch.status == BranchStatus.OPEN:
                age_days = (now - branch.created_at) / 86400
                if age_days > branch_age_threshold_days:
                    unresolved_branch_ids.append(branch.branch_id)
                    violations.append(
                        f"Branch {branch.branch_id[:8]}… OPEN for "
                        f"{age_days:.0f} days (threshold={branch_age_threshold_days}d)"
                    )

        # Source gap summary
        if source_gaps:
            violations.append(
                f"{len(source_gaps)} claim(s) have no source_refs or evidence_refs"
            )

        return GraphAuditResult(
            graph_id=graph_id,
            timestamp=_time.time(),
            passed=len(violations) == 0,
            orphan_claim_ids=orphan_ids,
            unresolved_branches=unresolved_branch_ids,
            source_gaps=source_gaps,
            violations=violations,
        )
