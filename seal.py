"""
Alexandria Core — seal.py
Sealing Process (Technical Annex D v2)

A Seal is an immutable, versioned snapshot of the epistemic graph
at a given point in time. It represents the transition from
VALIDATED to SEALED status for all eligible claims.

Seal Criteria (Annex D.4):
    D.1  No open HIGH-severity diffs (DiffResolutionRate check)
    D.2  All claims have source_refs (Source Traceability)
    D.3  No claims in FORMAL_ERROR status
    D.4  Patch chain integrity verified (SHA-256 check)
    D.5  Maturity level >= FUNCTIONAL (Φ >= 0.65)
    D.6  All VALIDATED claims have assumptions[] non-empty

Seal output:
    SealRecord — immutable snapshot descriptor stored in graph
    blockchain_anchor — SHA-256 of the full seal state
    All VALIDATED claims promoted to SEALED

Note on blockchain_anchor:
    We use a local SHA-256 hash of the seal state as the anchor.
    In production this would be submitted to a distributed ledger.
    The anchor is stored on every Patch and JudgmentNode created
    during this seal cycle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .schema import (
    ClaimNode, EpistemicStatus, Patch, PatchOperation,
    Category, BuilderOrigin,
)
from .patch import PatchChain, PatchEmitter
from .diff import DiffReport, DiffSeverity, DiffStatus
from .maturity import MaturityReport, MaturityLevel, MaturityCalculator

log = logging.getLogger(__name__)


# ── Seal criterion result ─────────────────────────────────────────────────────

@dataclass
class CriterionResult:
    criterion: str       # e.g. "D.1"
    name:      str
    passed:    bool
    detail:    str

    def __str__(self) -> str:
        mark = "✓" if self.passed else "✗"
        return f"  {mark} {self.criterion} {self.name:<35} {self.detail}"


# ── Seal record ───────────────────────────────────────────────────────────────

@dataclass
class SealRecord:
    """
    Immutable descriptor of a completed seal operation.
    Stored as a :Seal node in the graph.
    All sealed claims carry a reference to this record's seal_id.
    """
    seal_id:            str
    version:            int
    graph_id:           str
    blockchain_anchor:  str
    sealed_claim_ids:   list[str]
    patch_chain_length: int
    phi:                float
    maturity_level:     str
    timestamp:          float
    criteria:           list[dict]   # serialized CriterionResults

    @classmethod
    def new(
        cls,
        graph_id:          str,
        version:           int,
        blockchain_anchor: str,
        sealed_claim_ids:  list[str],
        patch_chain_length: int,
        phi:               float,
        maturity_level:    str,
        criteria:          list[CriterionResult],
    ) -> "SealRecord":
        return cls(
            seal_id            = str(uuid.uuid4()),
            version            = version,
            graph_id           = graph_id,
            blockchain_anchor  = blockchain_anchor,
            sealed_claim_ids   = sealed_claim_ids,
            patch_chain_length = patch_chain_length,
            phi                = phi,
            maturity_level     = maturity_level,
            timestamp          = time.time(),
            criteria           = [
                {
                    "criterion": c.criterion,
                    "name":      c.name,
                    "passed":    c.passed,
                    "detail":    c.detail,
                }
                for c in criteria
            ],
        )

    def to_dict(self) -> dict:
        return {
            "seal_id":            self.seal_id,
            "version":            self.version,
            "graph_id":           self.graph_id,
            "blockchain_anchor":  self.blockchain_anchor,
            "sealed_claim_count": len(self.sealed_claim_ids),
            "patch_chain_length": self.patch_chain_length,
            "phi":                self.phi,
            "maturity_level":     self.maturity_level,
            "timestamp":          self.timestamp,
        }


# ── Seal engine ───────────────────────────────────────────────────────────────

class SealEngine:
    """
    Executes the seal process (Annex D v2).

    Workflow:
        1. Check all six D criteria
        2. If all pass: compute blockchain_anchor, promote VALIDATED → SEALED
        3. Emit MODIFY patches for all promoted claims
        4. Create SealRecord
        5. Return SealResult

    Usage:
        engine = SealEngine()
        result = engine.seal(
            claims       = all_claims,
            diff_reports = all_diff_reports,
            patch_chain  = chain,
            emitter      = emitter,
            graph_id     = "alexandria_v1",
        )
        if result.success:
            print(result.seal_record)
    """

    def seal(
        self,
        claims:         list[ClaimNode],
        diff_reports:   list[DiffReport],
        patch_chain:    PatchChain,
        emitter:        PatchEmitter,
        graph_id:       str = "graph",
        version:        int = 1,
        force:          bool = False,   # bypass maturity check — for testing only
    ) -> "SealResult":
        """
        Attempt to seal the graph.
        Returns SealResult with success flag and full audit trail.
        """
        all_diffs = [d for r in diff_reports for d in r.diffs]

        # ── Compute maturity ──────────────────────────────────────────────────
        calc           = MaturityCalculator()
        maturity       = calc.assess(claims, diff_reports, patch_chain, graph_id)

        # ── Check D criteria ──────────────────────────────────────────────────
        criteria = [
            self._D1_no_open_high_diffs(all_diffs),
            self._D2_source_traceability(claims),
            self._D3_no_formal_errors(claims),
            self._D4_patch_chain_integrity(patch_chain),
            self._D5_maturity_threshold(maturity, force),
            self._D6_assumptions_present(claims),
        ]

        all_passed = all(c.passed for c in criteria)

        if not all_passed:
            failed = [c for c in criteria if not c.passed]
            log.warning(
                f"Seal REJECTED for graph={graph_id!r}: "
                f"{len(failed)} criterion/criteria failed."
            )
            return SealResult(
                success=False,
                graph_id=graph_id,
                criteria=criteria,
                maturity=maturity,
                seal_record=None,
                sealed_count=0,
                rejection_reasons=[c.detail for c in failed],
            )

        # ── Promote VALIDATED → SEALED ────────────────────────────────────────
        eligible = [
            c for c in claims
            if c.status == EpistemicStatus.VALIDATED
        ]

        sealed_ids = []
        for claim in eligible:
            claim.status = EpistemicStatus.SEALED
            try:
                emitter.modify(
                    claim,
                    changed_fields={
                        "claim_id": claim.claim_id,
                        "status":   EpistemicStatus.SEALED.value,
                    },
                )
                sealed_ids.append(claim.claim_id)
            except Exception as e:
                log.error(f"Failed to emit MODIFY patch for {claim.claim_id[:8]}…: {e}")

        # ── Compute blockchain anchor ─────────────────────────────────────────
        anchor = self._compute_anchor(
            sealed_ids    = sealed_ids,
            chain_head    = patch_chain.head_hash,
            graph_id      = graph_id,
            version       = version,
            timestamp     = time.time(),
        )

        # ── Create SealRecord ─────────────────────────────────────────────────
        record = SealRecord.new(
            graph_id           = graph_id,
            version            = version,
            blockchain_anchor  = anchor,
            sealed_claim_ids   = sealed_ids,
            patch_chain_length = patch_chain.length,
            phi                = maturity.phi,
            maturity_level     = maturity.level.value,
            criteria           = criteria,
        )

        log.info(
            f"Seal SUCCESS: graph={graph_id!r} version={version} "
            f"sealed={len(sealed_ids)} claims "
            f"Φ={maturity.phi:.3f} anchor={anchor[:16]}…"
        )

        return SealResult(
            success=True,
            graph_id=graph_id,
            criteria=criteria,
            maturity=maturity,
            seal_record=record,
            sealed_count=len(sealed_ids),
            rejection_reasons=[],
        )

    # ── Criteria ──────────────────────────────────────────────────────────────

    def _D1_no_open_high_diffs(self, diffs: list) -> CriterionResult:
        """D.1: No unresolved HIGH-severity diffs."""
        open_high = [
            d for d in diffs
            if d.severity == DiffSeverity.HIGH and d.status == DiffStatus.OPEN
        ]
        passed = len(open_high) == 0
        return CriterionResult(
            criterion = "D.1",
            name      = "No open HIGH diffs",
            passed    = passed,
            detail    = (
                "OK" if passed else
                f"{len(open_high)} HIGH diff(s) still OPEN: "
                f"{[d.diff_type.value for d in open_high[:3]]}"
            ),
        )

    def _D2_source_traceability(self, claims: list[ClaimNode]) -> CriterionResult:
        """D.2: All VALIDATED/SEALED claims have source_refs."""
        eligible = [
            c for c in claims
            if c.status in (EpistemicStatus.VALIDATED, EpistemicStatus.SEALED)
        ]
        missing = [
            c for c in eligible
            if not c.source_refs and not c.evidence_refs
        ]
        passed = len(missing) == 0
        return CriterionResult(
            criterion = "D.2",
            name      = "Source traceability",
            passed    = passed,
            detail    = (
                f"OK ({len(eligible)} eligible claims)"
                if passed else
                f"{len(missing)} claim(s) missing source_refs: "
                f"{[c.claim_id[:8]+'…' for c in missing[:3]]}"
            ),
        )

    def _D3_no_formal_errors(self, claims: list[ClaimNode]) -> CriterionResult:
        """D.3: No claims in FORMAL_ERROR status."""
        errors = [c for c in claims if c.status == EpistemicStatus.FORMAL_ERROR]
        passed = len(errors) == 0
        return CriterionResult(
            criterion = "D.3",
            name      = "No FORMAL_ERROR claims",
            passed    = passed,
            detail    = (
                "OK" if passed else
                f"{len(errors)} claim(s) in FORMAL_ERROR — "
                "must be resolved or removed before sealing."
            ),
        )

    def _D4_patch_chain_integrity(self, chain: PatchChain) -> CriterionResult:
        """D.4: SHA-256 patch chain is intact."""
        if chain.length == 0:
            return CriterionResult(
                criterion = "D.4",
                name      = "Patch chain integrity",
                passed    = True,
                detail    = "OK (empty chain — genesis state)",
            )
        ok, violations = chain.verify_integrity()
        return CriterionResult(
            criterion = "D.4",
            name      = "Patch chain integrity",
            passed    = ok,
            detail    = (
                f"OK ({chain.length} patches verified)"
                if ok else
                f"TAMPERED: {len(violations)} violation(s): {violations[:2]}"
            ),
        )

    def _D5_maturity_threshold(
        self, maturity: MaturityReport, force: bool
    ) -> CriterionResult:
        """
        D.5: Operational Readiness advisory — NOT a hard seal criterion.

        Sprint 1 fix (v2.2): Maturity (Φ) is decoupled from seal admissibility.

        Seal is a formal admissibility decision based on structural invariants
        (D.1–D.4, D.6). Maturity is an operational readiness metric — useful
        for prioritization and deployment decisions, but not a protocol norm.

        Conflating the two would allow a heuristic score (weighted sum of
        proxies) to block or permit a formally correct seal — which violates
        the principle that protocol norms must be explicit and non-heuristic.

        D.5 is therefore recorded as ADVISORY ONLY:
          - always passes (does not block sealing)
          - Φ value is logged for transparency
          - Maturity level is included in SealRecord for operational use

        Callers who need operational readiness gating should check
        MaturityReport.level >= MaturityLevel.FUNCTIONAL separately,
        outside the seal admissibility decision.
        """
        if maturity.phi < 0.65:
            detail = (
                f"ADVISORY: Maturity Phi={maturity.phi:.4f} is below 0.65 (FUNCTIONAL). "
                "Seal proceeds, but operational deployment requires caution. "
                "Maturity is decoupled from seal admissibility (v2.2 Sprint 1)."
            )
        else:
            detail = (
                f"ADVISORY: Maturity Phi={maturity.phi:.4f} "
                f"(>= 0.65, {maturity.level.value}). "
                "Maturity is decoupled from seal admissibility (v2.2 Sprint 1)."
            )
        if force:
            detail = f"FORCED override active. {detail}"

        return CriterionResult(
            criterion = "D.5",
            name      = "Operational Readiness (advisory, non-blocking)",
            passed    = True,   # always passes — advisory only
            detail    = detail,
        )

    def _D6_assumptions_present(self, claims: list[ClaimNode]) -> CriterionResult:
        """D.6: All VALIDATED claims have non-empty assumptions[]."""
        eligible = [
            c for c in claims
            if c.status == EpistemicStatus.VALIDATED
        ]
        missing = [c for c in eligible if not c.assumptions]
        passed  = len(missing) == 0
        return CriterionResult(
            criterion = "D.6",
            name      = "Assumptions present",
            passed    = passed,
            detail    = (
                f"OK ({len(eligible)} VALIDATED claims checked)"
                if passed else
                f"{len(missing)} VALIDATED claim(s) missing assumptions[]: "
                f"{[c.claim_id[:8]+'…' for c in missing[:3]]}"
            ),
        )

    # ── Anchor computation ────────────────────────────────────────────────────

    @staticmethod
    def _compute_anchor(
        sealed_ids: list[str],
        chain_head: str,
        graph_id:   str,
        version:    int,
        timestamp:  float,
    ) -> str:
        """
        Compute blockchain_anchor = SHA-256 of seal state.
        Deterministic: same inputs → same anchor.
        In production: submit this hash to a distributed ledger.
        """
        payload = {
            "graph_id":   graph_id,
            "version":    version,
            "timestamp":  timestamp,
            "chain_head": chain_head,
            "sealed_ids": sorted(sealed_ids),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()


# ── Seal result ───────────────────────────────────────────────────────────────

@dataclass
class SealResult:
    success:           bool
    graph_id:          str
    criteria:          list[CriterionResult]
    maturity:          MaturityReport
    seal_record:       Optional[SealRecord]
    sealed_count:      int
    rejection_reasons: list[str]

    def __str__(self) -> str:
        lines = [
            f"SealResult — graph={self.graph_id}",
            f"  {'SUCCESS ✓' if self.success else 'REJECTED ✗'}",
            f"  Sealed claims: {self.sealed_count}",
            f"  Φ = {self.maturity.phi:.4f}  ({self.maturity.level.value})",
            "",
            "  Criteria:",
        ]
        for c in self.criteria:
            lines.append(str(c))
        if self.seal_record:
            lines += [
                "",
                f"  Seal ID:  {self.seal_record.seal_id[:16]}…",
                f"  Anchor:   {self.seal_record.blockchain_anchor[:32]}…",
            ]
        if self.rejection_reasons:
            lines += ["", "  Rejection reasons:"]
            for r in self.rejection_reasons:
                lines.append(f"    · {r}")
        return "\n".join(lines)
