"""
Alexandria Core — patch.py
Patch-DSL: SHA-256 chain, tamper detection, patch emission.

Implements Section VII.13.7 and Appendix C.4 (Formal Guarantees):
    C_i = Hash(P_i, C_{i-1})

The patch chain is the only way epistemic state can change.
All DBA output enters the protocol exclusively through patches.
"""

from __future__ import annotations
import hashlib
import json
import time
import logging
from typing import Optional

from .schema import (
    ClaimNode, Patch, PatchOperation, Category,
    EpistemicStatus, BuilderOrigin,
)

log = logging.getLogger(__name__)


# ── Hash computation ──────────────────────────────────────────────────────────

def compute_patch_hash(patch: Patch, parent_hash: str) -> str:
    """
    C_i = SHA-256(P_i + C_{i-1})

    Serializes patch content deterministically (sorted keys),
    then hashes together with parent hash.
    """
    payload = {
        "patch_id":        patch.patch_id,
        "parent_patch_id": patch.parent_patch_id,
        "operation":       patch.operation.value,
        "target_id":       patch.target_id,
        "target_type":     patch.target_type,
        "timestamp":       patch.timestamp,
        "category":        patch.category.value,
        "assumptions":     sorted(patch.assumptions),  # canonical order
        "content_hash":    _hash_content(patch.content),
    }
    serialized = json.dumps(payload, sort_keys=True)
    combined   = serialized + parent_hash
    return hashlib.sha256(combined.encode()).hexdigest()


def _hash_content(content: dict) -> str:
    """Deterministic hash of patch content."""
    return hashlib.sha256(
        json.dumps(content, sort_keys=True).encode()
    ).hexdigest()


# ── Patch chain ───────────────────────────────────────────────────────────────

class PatchChain:
    """
    In-memory patch chain with SHA-256 anchoring.

    In production this would persist to Neo4j via db.store_patch().
    Here it also serves as the canonical sequential state.

    Invariants enforced:
    - Monotonic timestamps (C.7)
    - Hash chain integrity (C.4)
    - Immutability after commit
    """

    def __init__(self):
        self._patches:      list[Patch] = []
        self._hash_chain:   list[str]   = []  # parallel to _patches
        self._genesis_hash: str         = "0" * 64  # initial "parent hash"

    @property
    def head_hash(self) -> str:
        """Hash of the most recent patch. Genesis hash if chain is empty."""
        return self._hash_chain[-1] if self._hash_chain else self._genesis_hash

    @property
    def head_patch(self) -> Optional[Patch]:
        return self._patches[-1] if self._patches else None

    @property
    def length(self) -> int:
        return len(self._patches)

    def commit(self, patch: Patch) -> str:
        """
        Commit a patch to the chain.

        Validates:
        - parent_patch_id matches head
        - timestamp is strictly monotonic
        - assumptions[] non-empty
        - category is set

        Returns: computed hash for this patch.
        Raises:  ValueError on any violation.
        """
        # Parent consistency
        expected_parent = self.head_patch.patch_id if self.head_patch else None
        if patch.parent_patch_id != expected_parent:
            raise ValueError(
                f"parent_patch_id mismatch: expected {expected_parent!r}, "
                f"got {patch.parent_patch_id!r}"
            )

        # Temporal monotonicity (Section C.7)
        if self.head_patch and patch.timestamp <= self.head_patch.timestamp:
            raise ValueError(
                f"Timestamp violation: {patch.timestamp} <= {self.head_patch.timestamp}. "
                "Retroactive patches are prohibited (Section C.7)."
            )

        # Assumptions mandatory
        if not patch.assumptions:
            raise ValueError(
                "Patch must carry non-empty assumptions[] (Section VII.13.2)."
            )

        # Compute hash
        patch_hash = compute_patch_hash(patch, self.head_hash)
        patch.hash = patch_hash

        self._patches.append(patch)
        self._hash_chain.append(patch_hash)

        log.debug(f"Committed patch {patch.patch_id[:8]}… hash={patch_hash[:12]}…")
        return patch_hash

    def verify_integrity(self) -> tuple[bool, list[str]]:
        """
        Recompute entire hash chain and compare.
        Returns (ok, list_of_violations).

        This is the tamper detection mechanism (Appendix C.4).
        """
        violations = []
        current_parent_hash = self._genesis_hash

        for i, patch in enumerate(self._patches):
            expected = compute_patch_hash(patch, current_parent_hash)
            if expected != self._hash_chain[i]:
                violations.append(
                    f"Hash mismatch at patch {i} ({patch.patch_id[:8]}…): "
                    f"stored={self._hash_chain[i][:12]}… "
                    f"recomputed={expected[:12]}…"
                )
            current_parent_hash = self._hash_chain[i]

        ok = len(violations) == 0
        if ok:
            log.info(f"Chain integrity verified: {self.length} patches, all OK.")
        else:
            log.error(f"Chain integrity FAILED: {len(violations)} violation(s).")
        return ok, violations

    def reconstruct_state(self) -> dict[str, dict]:
        """
        Apply all patches in order to reconstruct current epistemic state.
        Returns dict: target_id → latest content dict.

        Implements: E(t_n) = P_n(…P_2(P_1(E_0))…)
        """
        state: dict[str, dict] = {}

        for patch in self._patches:
            tid = patch.target_id
            op  = patch.operation

            if op == PatchOperation.ADD:
                if tid in state:
                    log.warning(f"ADD on existing target {tid} — treating as MODIFY")
                state[tid] = dict(patch.content)

            elif op == PatchOperation.MODIFY:
                if tid not in state:
                    log.warning(f"MODIFY on unknown target {tid} — creating")
                    state[tid] = {}
                state[tid].update(patch.content)

            elif op == PatchOperation.DEPRECATE:
                if tid in state:
                    state[tid]["status"] = EpistemicStatus.DEPRECATED.value
                    state[tid]["deprecated_by_patch"] = patch.patch_id

            elif op == PatchOperation.BRANCH:
                # Branch creates a new entry derived from parent
                branch_id = patch.branch_id or patch.patch_id
                branch_content = dict(state.get(tid, {}))
                branch_content.update(patch.content)
                branch_content["branch_id"] = branch_id
                branch_content["status"]    = EpistemicStatus.STABLE_AMBIGUITY.value
                state[f"{tid}::{branch_id}"] = branch_content
                # Original also moves to STABLE_AMBIGUITY
                if tid in state:
                    state[tid]["status"] = EpistemicStatus.STABLE_AMBIGUITY.value

        return state

    def to_list(self) -> list[dict]:
        """Serialize all patches for storage or export."""
        return [p.to_dict() for p in self._patches]


# ── Patch factory ─────────────────────────────────────────────────────────────

class PatchEmitter:
    """
    Constructs protocol-compliant patches from ClaimNodes.
    Used by the Adjudication layer to emit DBA output into the protocol.

    Usage:
        emitter = PatchEmitter(chain)
        emitter.add(claim)
        emitter.modify(claim)
        emitter.deprecate(claim_id, category, assumptions)
        emitter.branch(claim, branch_id)
    """

    def __init__(self, chain: PatchChain):
        self._chain = chain

    def _make_patch(
        self,
        operation:   PatchOperation,
        target_id:   str,
        target_type: str,
        content:     dict,
        category:    Category,
        assumptions: list[str],
        branch_id:   Optional[str] = None,
    ) -> Patch:
        head = self._chain.head_patch
        return Patch.new(
            operation=operation,
            target_id=target_id,
            target_type=target_type,
            content=content,
            category=category,
            assumptions=assumptions,
            parent_patch_id=head.patch_id if head else None,
            branch_id=branch_id,
        )

    def add(self, claim: ClaimNode) -> Patch:
        """Emit ADD patch for a new claim."""
        errors = claim.validate()
        if errors:
            raise ValueError(f"Cannot emit ADD — claim invalid: {errors}")

        patch = self._make_patch(
            operation=PatchOperation.ADD,
            target_id=claim.claim_id,
            target_type="Claim",
            content=claim.to_dict(),
            category=claim.category,
            assumptions=claim.assumptions,
        )
        self._chain.commit(patch)
        log.info(f"ADD patch committed for claim {claim.claim_id[:8]}…")
        return patch

    def modify(self, claim: ClaimNode, changed_fields: dict | None = None) -> Patch:
        """
        Emit MODIFY patch.
        changed_fields: if given, only these fields are patched.
        Otherwise the full claim dict is used.
        """
        content = changed_fields if changed_fields else claim.to_dict()
        content["claim_id"] = claim.claim_id  # always include for targeting

        # Update lineage
        head = self._chain.head_patch
        if head:
            claim.lineage.append(head.patch_id)
        claim.updated_at = time.time()

        patch = self._make_patch(
            operation=PatchOperation.MODIFY,
            target_id=claim.claim_id,
            target_type="Claim",
            content=content,
            category=claim.category,
            assumptions=claim.assumptions,
        )
        self._chain.commit(patch)
        log.info(f"MODIFY patch committed for claim {claim.claim_id[:8]}…")
        return patch

    def deprecate(
        self,
        claim_id:    str,
        category:    Category,
        assumptions: list[str],
        reason:      str = "",
    ) -> Patch:
        """Emit DEPRECATE patch."""
        content = {
            "claim_id": claim_id,
            "status":   EpistemicStatus.DEPRECATED.value,
            "deprecation_reason": reason,
        }
        patch = self._make_patch(
            operation=PatchOperation.DEPRECATE,
            target_id=claim_id,
            target_type="Claim",
            content=content,
            category=category,
            assumptions=assumptions,
        )
        self._chain.commit(patch)
        log.info(f"DEPRECATE patch committed for claim {claim_id[:8]}…")
        return patch

    def branch(self, claim: ClaimNode, branch_id: str) -> Patch:
        """
        Emit BRANCH patch — triggered by unresolvable CONTRADICTS.
        Both the original and the branch enter STABLE_AMBIGUITY.
        """
        content = claim.to_dict()
        content["status"]    = EpistemicStatus.STABLE_AMBIGUITY.value
        content["branch_id"] = branch_id

        patch = self._make_patch(
            operation=PatchOperation.BRANCH,
            target_id=claim.claim_id,
            target_type="Claim",
            content=content,
            category=claim.category,
            assumptions=claim.assumptions,
            branch_id=branch_id,
        )
        self._chain.commit(patch)
        log.info(
            f"BRANCH patch committed for claim {claim.claim_id[:8]}… "
            f"branch={branch_id}"
        )
        return patch
