"""
Alexandria Core — schema.py
Node types, relation types, and field definitions.
Based on Technical Annex A (v2) of the Alexandria Protocol.

All classes are pure Python dataclasses — no DB dependency.
They serve as the canonical in-memory representation before
writing to or reading from Neo4j.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import uuid


# ── Enumerations ─────────────────────────────────────────────────────────────

class Category(str, Enum):
    """Section III.3 — epistemic category of a claim."""
    EMPIRICAL   = "EMPIRICAL"
    NORMATIVE   = "NORMATIVE"
    MODEL       = "MODEL"
    SPECULATIVE = "SPECULATIVE"


class Modality(str, Enum):
    """Epistemic strength of a claim."""
    HYPOTHESIS  = "hypothesis"
    SUGGESTION  = "suggestion"
    EVIDENCE    = "evidence"
    ESTABLISHED = "established"


class EpistemicStatus(str, Enum):
    """Protocol status classes (Section X.8 + Annex A v2)."""
    UNVALIDATED     = "UNVALIDATED"
    VALIDATED       = "VALIDATED"
    SEALED          = "SEALED"
    STABLE_AMBIGUITY = "STABLE_AMBIGUITY"
    FORMAL_ERROR    = "FORMAL_ERROR"
    DEPRECATED      = "DEPRECATED"


class BuilderOrigin(str, Enum):
    """Which builder produced this node (DBA extension, V-A.5)."""
    ALPHA       = "alpha"
    BETA        = "beta"
    ADJUDICATED = "adjudicated"


class PatchOperation(str, Enum):
    """Patch-DSL operations (Section VII.13.7)."""
    ADD       = "ADD"
    MODIFY    = "MODIFY"
    DEPRECATE = "DEPRECATE"
    BRANCH    = "BRANCH"


class UncertaintyType(str, Enum):
    """Section VII.4."""
    PROBABILISTIC = "probabilistic"
    DETERMINISTIC = "deterministic"


# ── Sub-structures ────────────────────────────────────────────────────────────

@dataclass
class Uncertainty:
    """
    Non-aggregative uncertainty tuple (Section VII.4, Annex A v2).
    Required for all probabilistic claims. Absent for deterministic.
    """
    sigma: float                        # standard deviation
    ci:    tuple[float, float]          # confidence interval [low, high]
    n:     int                          # sample size
    type:  UncertaintyType = UncertaintyType.PROBABILISTIC

    def validate(self) -> list[str]:
        errors = []
        if not (0.0 <= self.sigma):
            errors.append("sigma must be >= 0")
        if not (self.ci[0] <= self.ci[1]):
            errors.append("ci[0] must be <= ci[1]")
        if self.n < 1:
            errors.append("n must be >= 1")
        return errors

    def to_dict(self) -> dict:
        return {
            "sigma": self.sigma,
            "ci_low": self.ci[0],
            "ci_high": self.ci[1],
            "n": self.n,
            "type": self.type.value,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Uncertainty":
        return cls(
            sigma=d["sigma"],
            ci=(d["ci_low"], d["ci_high"]),
            n=d["n"],
            type=UncertaintyType(d.get("type", "probabilistic")),
        )


@dataclass
class Validation:
    """Validation metadata (Section X, Annex A v2)."""
    validated: bool
    decay:     float        # λ decay constant (must be > 0, Section XI.4)
    refs:      list[str] = field(default_factory=list)  # e.g. ["VAL_EMPIRICAL_REPLICATION"]

    def validate(self) -> list[str]:
        errors = []
        if self.decay <= 0:
            errors.append("decay (λ) must be > 0 (Section XI.4)")
        return errors

    def to_dict(self) -> dict:
        return {"validated": self.validated, "decay": self.decay, "refs": self.refs}

    @classmethod
    def from_dict(cls, d: dict) -> "Validation":
        return cls(validated=d["validated"], decay=d["decay"], refs=d.get("refs", []))


# ── Node Types ────────────────────────────────────────────────────────────────

@dataclass
class EntityNode:
    """
    Concrete real-world entity.
    Examples: organizations, persons, locations, systems.
    """
    entity_id:   str
    name:        str
    entity_type: str                    # e.g. "Organization", "Person", "System"
    aliases:     list[str] = field(default_factory=list)
    description: str = ""
    source_refs: list[str] = field(default_factory=list)
    created_at:  float = field(default_factory=time.time)

    @classmethod
    def new(cls, name: str, entity_type: str, **kwargs) -> "EntityNode":
        return cls(entity_id=str(uuid.uuid4()), name=name, entity_type=entity_type, **kwargs)

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "entity_type": self.entity_type,
            "aliases": self.aliases,
            "description": self.description,
            "source_refs": self.source_refs,
            "created_at": self.created_at,
        }


@dataclass
class ConceptNode:
    """
    Abstract concept, class, or category (from OpenCyc ontology).
    """
    concept_id:  str
    name:        str
    definition:  str = ""
    broader:     list[str] = field(default_factory=list)   # parent concept IDs
    narrower:    list[str] = field(default_factory=list)   # child concept IDs
    source_refs: list[str] = field(default_factory=list)
    created_at:  float = field(default_factory=time.time)

    @classmethod
    def new(cls, name: str, **kwargs) -> "ConceptNode":
        return cls(concept_id=str(uuid.uuid4()), name=name, **kwargs)

    def to_dict(self) -> dict:
        return {
            "concept_id": self.concept_id,
            "name": self.name,
            "definition": self.definition,
            "broader": self.broader,
            "narrower": self.narrower,
            "source_refs": self.source_refs,
            "created_at": self.created_at,
        }


@dataclass
class WorkNode:
    """
    Scientific publication, dataset, or document (from OpenAlex).
    """
    work_id:      str
    title:        str
    doi:          str = ""
    year:         Optional[int] = None
    venue:        str = ""
    author_ids:   list[str] = field(default_factory=list)
    openalex_id:  str = ""
    source_refs:  list[str] = field(default_factory=list)
    created_at:   float = field(default_factory=time.time)

    @classmethod
    def new(cls, title: str, **kwargs) -> "WorkNode":
        return cls(work_id=str(uuid.uuid4()), title=title, **kwargs)

    def to_dict(self) -> dict:
        return {
            "work_id": self.work_id,
            "title": self.title,
            "doi": self.doi,
            "year": self.year,
            "venue": self.venue,
            "author_ids": self.author_ids,
            "openalex_id": self.openalex_id,
            "source_refs": self.source_refs,
            "created_at": self.created_at,
        }


@dataclass
class AuthorNode:
    """
    Author of a scientific work.
    """
    author_id:      str
    name:           str
    orcid:          str = ""
    institution_id: str = ""
    openalex_id:    str = ""
    created_at:     float = field(default_factory=time.time)

    @classmethod
    def new(cls, name: str, **kwargs) -> "AuthorNode":
        return cls(author_id=str(uuid.uuid4()), name=name, **kwargs)

    def to_dict(self) -> dict:
        return {
            "author_id": self.author_id,
            "name": self.name,
            "orcid": self.orcid,
            "institution_id": self.institution_id,
            "openalex_id": self.openalex_id,
            "created_at": self.created_at,
        }


@dataclass
class InstitutionNode:
    """
    Research institution or organization.
    """
    institution_id: str
    name:           str
    country:        str = ""
    ror_id:         str = ""
    openalex_id:    str = ""
    created_at:     float = field(default_factory=time.time)

    @classmethod
    def new(cls, name: str, **kwargs) -> "InstitutionNode":
        return cls(institution_id=str(uuid.uuid4()), name=name, **kwargs)

    def to_dict(self) -> dict:
        return {
            "institution_id": self.institution_id,
            "name": self.name,
            "country": self.country,
            "ror_id": self.ror_id,
            "openalex_id": self.openalex_id,
            "created_at": self.created_at,
        }


@dataclass
class EvidenceNode:
    """
    Concrete piece of evidence supporting or contradicting a claim.
    """
    evidence_id:  str
    text:         str                   # quoted text or data summary
    source_ref:   str                   # work_id or external URI
    location:     str = ""             # page, section, DOI fragment
    uncertainty:  Optional[Uncertainty] = None
    created_at:   float = field(default_factory=time.time)

    @classmethod
    def new(cls, text: str, source_ref: str, **kwargs) -> "EvidenceNode":
        return cls(evidence_id=str(uuid.uuid4()), text=text, source_ref=source_ref, **kwargs)

    def to_dict(self) -> dict:
        d = {
            "evidence_id": self.evidence_id,
            "text": self.text,
            "source_ref": self.source_ref,
            "location": self.location,
            "created_at": self.created_at,
        }
        if self.uncertainty:
            d["uncertainty"] = self.uncertainty.to_dict()
        return d


@dataclass
class ClaimNode:
    """
    Core epistemic unit of the Alexandria graph.
    Corresponds to Technical Annex E (v2) — full protocol-compliant schema.

    A claim asserts a typed relation between subject and object,
    with explicit assumptions, uncertainty, status, and lineage.
    """
    # Identity
    claim_id:       str
    version:        int = 1

    # Proposition
    subject:        str = ""            # node ID or literal
    predicate:      str = ""            # relation type from Annex F
    object:         str = ""            # node ID or literal

    # Epistemic classification (Section III.3)
    category:       Category = Category.EMPIRICAL
    modality:       Modality = Modality.HYPOTHESIS

    # Scope and qualifiers
    qualifiers:     dict = field(default_factory=dict)
    scope:          dict = field(default_factory=dict)
    time_scope:     dict = field(default_factory=dict)

    # Uncertainty — mandatory for probabilistic claims (Section VII.4)
    uncertainty:    Optional[Uncertainty] = None

    # Protocol status (Section X.8)
    status:         EpistemicStatus = EpistemicStatus.UNVALIDATED

    # Mandatory: explicit assumptions (Section VII.13.2)
    assumptions:    list[str] = field(default_factory=list)

    # Evidence and sources
    evidence_refs:  list[str] = field(default_factory=list)
    source_refs:    list[str] = field(default_factory=list)

    # Validation metadata
    validation:     Optional[Validation] = None

    # DBA extension (Section V-A.5)
    builder_origin: BuilderOrigin = BuilderOrigin.ALPHA

    # Lineage — ordered patch references (Section XI.2)
    lineage:        list[str] = field(default_factory=list)

    # Timestamps
    created_at:     float = field(default_factory=time.time)
    updated_at:     float = field(default_factory=time.time)

    @classmethod
    def new(cls, subject: str, predicate: str, object: str,
            category: Category, **kwargs) -> "ClaimNode":
        return cls(
            claim_id=str(uuid.uuid4()),
            subject=subject,
            predicate=predicate,
            object=object,
            category=category,
            **kwargs,
        )

    def validate(self) -> list[str]:
        """
        Structural validation — does NOT assess truth.
        Returns list of protocol violations.
        """
        errors = []

        # Mandatory fields
        if not self.subject:
            errors.append("subject is required")
        if not self.predicate:
            errors.append("predicate is required")
        if not self.object:
            errors.append("object is required")

        # Assumptions mandatory (Section VII.13.2)
        if not self.assumptions:
            errors.append("assumptions[] must be non-empty (Section VII.13.2)")

        # Uncertainty: precise 3-condition rule (Sprint 3)
        # EMPIRICAL + evidence/established + causal predicate -> required
        if EpistemicIdentity.uncertainty_required(self):
            if self.uncertainty is None:
                errors.append(
                    f"uncertainty required for {self.category.value}/"
                    f"{self.modality.value}/{self.predicate} (Section VII.4)"
                )
            else:
                errors.extend(self.uncertainty.validate())

        # Normative claims must not use causal relations
        if self.category == Category.NORMATIVE and self.predicate in (
            "CAUSES", "CONTRIBUTES_TO", "CORRELATES_WITH"
        ):
            errors.append(
                f"NORMATIVE claim must not use causal relation {self.predicate} (Annex F.1.4)"
            )

        # Speculative claims must not use CAUSES
        if self.category == Category.SPECULATIVE and self.predicate == "CAUSES":
            errors.append(
                "SPECULATIVE claim must not use CAUSES (Annex F.1.4)"
            )

        # Validation decay check
        if self.validation:
            errors.extend(self.validation.validate())

        return errors

    def to_dict(self) -> dict:
        d = {
            "claim_id":       self.claim_id,
            "version":        self.version,
            "subject":        self.subject,
            "predicate":      self.predicate,
            "object":         self.object,
            "category":       self.category.value,
            "modality":       self.modality.value,
            "qualifiers":     self.qualifiers,
            "scope":          self.scope,
            "time_scope":     self.time_scope,
            "status":         self.status.value,
            "assumptions":    self.assumptions,
            "evidence_refs":  self.evidence_refs,
            "source_refs":    self.source_refs,
            "builder_origin": self.builder_origin.value,
            "lineage":        self.lineage,
            "created_at":     self.created_at,
            "updated_at":     self.updated_at,
        }
        if self.uncertainty:
            d["uncertainty"] = self.uncertainty.to_dict()
        if self.validation:
            d["validation"] = self.validation.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ClaimNode":
        unc = Uncertainty.from_dict(d["uncertainty"]) if "uncertainty" in d else None
        val = Validation.from_dict(d["validation"]) if "validation" in d else None
        return cls(
            claim_id=d["claim_id"],
            version=d.get("version", 1),
            subject=d["subject"],
            predicate=d["predicate"],
            object=d["object"],
            category=Category(d["category"]),
            modality=Modality(d["modality"]),
            qualifiers=d.get("qualifiers", {}),
            scope=d.get("scope", {}),
            time_scope=d.get("time_scope", {}),
            uncertainty=unc,
            status=EpistemicStatus(d["status"]),
            assumptions=d.get("assumptions", []),
            evidence_refs=d.get("evidence_refs", []),
            source_refs=d.get("source_refs", []),
            validation=val,
            builder_origin=BuilderOrigin(d.get("builder_origin", "alpha")),
            lineage=d.get("lineage", []),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )


@dataclass
class JudgmentNode:
    """
    Adjudication record (Section V-A.7).
    Every adjudication decision must produce a JudgmentNode.
    Carries blockchain_anchor (Seal Criterion D.5).
    """
    judgment_id:       str
    diff_type:         str              # from Annex B taxonomy
    claim_alpha_id:    str
    claim_beta_id:     str
    outcome:           str              # "convergence" | "refinement" | "stable_ambiguity" | "formal_error"
    winning_id:        Optional[str]    # claim_id that was adopted, if convergence
    reason:            str
    rule_applied:      str              # e.g. "C.7", "C.8", "C.9"
    blockchain_anchor: str = ""        # filled after commit
    created_at:        float = field(default_factory=time.time)

    @classmethod
    def new(cls, diff_type: str, claim_alpha_id: str, claim_beta_id: str,
            outcome: str, reason: str, rule_applied: str, **kwargs) -> "JudgmentNode":
        return cls(
            judgment_id=str(uuid.uuid4()),
            diff_type=diff_type,
            claim_alpha_id=claim_alpha_id,
            claim_beta_id=claim_beta_id,
            outcome=outcome,
            winning_id=None,
            reason=reason,
            rule_applied=rule_applied,
            **kwargs,
        )

    def to_dict(self) -> dict:
        return {
            "judgment_id":       self.judgment_id,
            "diff_type":         self.diff_type,
            "claim_alpha_id":    self.claim_alpha_id,
            "claim_beta_id":     self.claim_beta_id,
            "outcome":           self.outcome,
            "winning_id":        self.winning_id,
            "reason":            self.reason,
            "rule_applied":      self.rule_applied,
            "blockchain_anchor": self.blockchain_anchor,
            "created_at":        self.created_at,
        }


# ── Patch ─────────────────────────────────────────────────────────────────────

@dataclass
class Patch:
    """
    Atomic epistemic modification (Section VII.13.7, Annex D Seal Criteria).
    The ONLY way DBA output enters the Alexandria protocol.

    patch_id and parent_patch_id form the immutable hash chain:
        C_i = Hash(P_i, C_{i-1})
    """
    patch_id:         str
    parent_patch_id:  Optional[str]     # None for genesis patch
    operation:        PatchOperation
    target_id:        str               # node ID being modified
    target_type:      str               # "Claim" | "Entity" | "Concept" | etc.
    timestamp:        float
    content:          dict              # serialized node dict
    category:         Category
    assumptions:      list[str]
    blockchain_anchor: str = ""        # filled after commit
    branch_id:        Optional[str] = None
    hash:             str = ""         # SHA-256(patch_id + parent_hash + content)

    @classmethod
    def new(cls, operation: PatchOperation, target_id: str, target_type: str,
            content: dict, category: Category, assumptions: list[str],
            parent_patch_id: Optional[str] = None, **kwargs) -> "Patch":
        return cls(
            patch_id=str(uuid.uuid4()),
            parent_patch_id=parent_patch_id,
            operation=operation,
            target_id=target_id,
            target_type=target_type,
            timestamp=time.time(),
            content=content,
            category=category,
            assumptions=assumptions,
            **kwargs,
        )

    def to_dict(self) -> dict:
        return {
            "patch_id":          self.patch_id,
            "parent_patch_id":   self.parent_patch_id,
            "operation":         self.operation.value,
            "target_id":         self.target_id,
            "target_type":       self.target_type,
            "timestamp":         self.timestamp,
            "content":           self.content,
            "category":          self.category.value,
            "assumptions":       self.assumptions,
            "blockchain_anchor": self.blockchain_anchor,
            "branch_id":         self.branch_id,
            "hash":              self.hash,
        }


# ── Relation types (Annex F) ──────────────────────────────────────────────────

class RelationType(str, Enum):
    """
    All relation types from Technical Annex F.
    Grouped by family.
    """
    # F.2 — Causal scale (ordered by epistemic strength)
    MENTIONS          = "MENTIONS"
    RELATES_TO        = "RELATES_TO"
    CORRELATES_WITH   = "CORRELATES_WITH"
    PARTIALLY_SUPPORTS = "PARTIALLY_SUPPORTS"
    SUPPORTS          = "SUPPORTS"
    STRONGLY_SUPPORTS = "STRONGLY_SUPPORTS"
    CONTRIBUTES_TO    = "CONTRIBUTES_TO"
    CAUSES            = "CAUSES"

    # F.3 — Claim-to-claim
    CONTRADICTS           = "CONTRADICTS"
    PARTIALLY_CONTRADICTS = "PARTIALLY_CONTRADICTS"
    REFINES               = "REFINES"
    GENERALIZES           = "GENERALIZES"
    DERIVED_FROM          = "DERIVED_FROM"
    REPLACES              = "REPLACES"
    EXTENDS               = "EXTENDS"

    # F.4 — Evidence
    HAS_EVIDENCE     = "HAS_EVIDENCE"
    SUPPORTED_BY     = "SUPPORTED_BY"
    MENTIONED_IN     = "MENTIONED_IN"
    CONTRADICTED_BY  = "CONTRADICTED_BY"
    REPLICATED_BY    = "REPLICATED_BY"

    # F.5 — Ontological / scientific
    INSTANCE_OF  = "INSTANCE_OF"
    SUBCLASS_OF  = "SUBCLASS_OF"
    PART_OF      = "PART_OF"
    AUTHORED_BY  = "AUTHORED_BY"
    CITES        = "CITES"

    # DBA protocol relations (Annex A v2)
    PATCH_ANCHORS_TO = "PATCH_ANCHORS_TO"


# Adjudication priority order for causal scale (Annex F.6)
CAUSAL_SCALE_PRIORITY: list[RelationType] = [
    RelationType.MENTIONS,
    RelationType.RELATES_TO,
    RelationType.CORRELATES_WITH,
    RelationType.PARTIALLY_SUPPORTS,
    RelationType.SUPPORTS,
    RelationType.STRONGLY_SUPPORTS,
    RelationType.CONTRIBUTES_TO,
    RelationType.CAUSES,
]

def causal_priority(rel: RelationType) -> int:
    """Lower number = more defensive (wins in C.7 adjudication)."""
    try:
        return CAUSAL_SCALE_PRIORITY.index(rel)
    except ValueError:
        return -1  # non-causal relation


# ── Branch — First-Class Graph Object (v2.2, Sprint 1) ───────────────────────

class BranchStatus(str, Enum):
    """Lifecycle status of a Branch node."""
    OPEN       = "open"        # active, no merge decision yet
    MERGED     = "merged"      # one branch adopted, other deprecated
    DEPRECATED = "deprecated"  # branch superseded or abandoned
    ARCHIVED   = "archived"    # sealed and preserved for audit


class MergePolicy(str, Enum):
    """
    Conditions under which a branch may be merged.
    Protocol invariant: merging requires explicit, auditable justification —
    never implicit resolution. (Section V-A invariant, Sprint 1 fix)
    """
    NEVER              = "never"              # branch must remain permanently open
    ON_NEW_EVIDENCE    = "on_new_evidence"    # merge admissible if new evidence resolves conflict
    ON_SCOPE_CHANGE    = "on_scope_change"    # merge if scope conditions change
    ON_RULE_EXTENSION  = "on_rule_extension"  # merge if adjudication rulebook extended
    HUMAN_REVIEW_ONLY  = "human_review_only"  # merge requires explicit human adjudication


@dataclass
class BranchNode:
    """
    First-class representation of an epistemic branch.

    A Branch is created whenever adjudication cannot resolve a conflict
    by an explicit rule (C.3 UNRESOLVED_PENDING_RULE, C.7 CAUSALITY_MISMATCH,
    C.8 category incompatibility, C.9 assumption separation).

    Protocol invariant: Branching is the NORMAL resolution for unresolvable
    dissent — not an exception or failure mode. (Section V-A)

    Fields
    ------
    branch_id         UUID of this branch
    parent_branch_id  UUID of parent branch (None = root / main branch)
    trigger_diff_ids  DiffNode IDs that caused this branch
    branch_reason     Human-readable reason (from DiffType + rule)
    branch_scope      Scope within which the branch is relevant
                      (e.g. {"domain": "climate", "region": "global"})
    claim_alpha_id    Alpha builder's claim in this branch
    claim_beta_id     Beta builder's claim in this branch
    merge_policy      Under what conditions merge is admissible
    status            Current lifecycle status
    created_at        Unix timestamp
    deprecated_at     Set when status → DEPRECATED or ARCHIVED
    deprecation_reason  Why the branch was deprecated (if applicable)
    """
    branch_id:         str
    parent_branch_id:  Optional[str]
    trigger_diff_ids:  list[str]
    branch_reason:     str
    branch_scope:      dict
    claim_alpha_id:    str
    claim_beta_id:     str
    merge_policy:      MergePolicy
    status:            BranchStatus
    created_at:        float
    deprecated_at:     Optional[float] = None
    deprecation_reason: Optional[str] = None

    @classmethod
    def new(
        cls,
        trigger_diff_ids: list[str],
        branch_reason:    str,
        claim_alpha_id:   str,
        claim_beta_id:    str,
        parent_branch_id: Optional[str] = None,
        branch_scope:     Optional[dict] = None,
        merge_policy:     MergePolicy = MergePolicy.ON_NEW_EVIDENCE,
    ) -> "BranchNode":
        return cls(
            branch_id        = str(uuid.uuid4()),
            parent_branch_id = parent_branch_id,
            trigger_diff_ids = trigger_diff_ids,
            branch_reason    = branch_reason,
            branch_scope     = branch_scope or {},
            claim_alpha_id   = claim_alpha_id,
            claim_beta_id    = claim_beta_id,
            merge_policy     = merge_policy,
            status           = BranchStatus.OPEN,
            created_at       = time.time(),
        )

    def deprecate(self, reason: str) -> None:
        self.status           = BranchStatus.DEPRECATED
        self.deprecated_at    = time.time()
        self.deprecation_reason = reason

    def archive(self) -> None:
        self.status        = BranchStatus.ARCHIVED
        self.deprecated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "branch_id":          self.branch_id,
            "parent_branch_id":   self.parent_branch_id,
            "trigger_diff_ids":   self.trigger_diff_ids,
            "branch_reason":      self.branch_reason,
            "branch_scope":       self.branch_scope,
            "claim_alpha_id":     self.claim_alpha_id,
            "claim_beta_id":      self.claim_beta_id,
            "merge_policy":       self.merge_policy.value,
            "status":             self.status.value,
            "created_at":         self.created_at,
            "deprecated_at":      self.deprecated_at,
            "deprecation_reason": self.deprecation_reason,
        }


# ── Epistemic Identity Doctrine (v2.2 Sprint 2) ───────────────────────────────

class EpistemicIdentity:
    """
    Formal definition of epistemic identity in the Alexandria graph.

    Sprint 2 fix: makes explicit what was previously only implicit in the code.

    Protocol invariant [SHALL]:
        The epistemic primary unit is NOT the Claim alone.
        The epistemic primary unit is Claim + Lineage + Patch-History.

        Claims are readable epistemic states.
        Patches are the only valid state transitions.
        Reconstructibility arises from Claim + Lineage + PatchChain together.

    This has direct consequences for Audit Block II (path reconstruction):
        An audit of a Claim without its Lineage is incomplete.
        A Claim with empty lineage[] is only valid if it has no prior history
        (i.e. it was just created by PatchEmitter.add()).

    Uncertainty admissibility rules [SHALL]:
        uncertainty is REQUIRED when:
            - category = EMPIRICAL and modality in {evidence, established}
            - predicate is on the causal scale (RelationType with causal semantics)
            - claim is statistically inferred (source_refs point to empirical works)

        uncertainty is NOT required when:
            - category = MODEL or SPECULATIVE (structural, not empirical variance)
            - category = NORMATIVE (value claims, not probabilistic)
            - predicate = MENTIONS, RELATES_TO (no causal/statistical claim made)
            - modality = hypothesis, suggestion (insufficient evidence for statistics)

        This rule prevents two failure modes:
            1. False audit errors on structural claims (over-triggering)
            2. Missing uncertainty on empirical claims (under-triggering)
    """

    # Predicates that REQUIRE uncertainty when used with EMPIRICAL category
    CAUSAL_EMPIRICAL_PREDICATES: frozenset = frozenset({
        "CORRELATES_WITH",
        "PARTIALLY_SUPPORTS",
        "SUPPORTS",
        "STRONGLY_SUPPORTS",
        "CONTRIBUTES_TO",
        "CAUSES",
    })

    # Modalities that trigger uncertainty requirement for EMPIRICAL claims
    EVIDENCE_MODALITIES: frozenset = frozenset({
        "evidence",
        "established",
    })

    # Categories where uncertainty is structurally inapplicable
    NON_PROBABILISTIC_CATEGORIES: frozenset = frozenset({
        "NORMATIVE",
        "MODEL",
        "SPECULATIVE",
    })

    @staticmethod
    def uncertainty_required(claim: "ClaimNode") -> bool:
        """
        Returns True if this claim MUST carry an uncertainty tuple.

        Rule: EMPIRICAL + evidence/established modality + causal/statistical predicate
              → uncertainty required.
        All other cases → uncertainty optional (but encouraged for EMPIRICAL/hypothesis).
        """
        if claim.category.value in EpistemicIdentity.NON_PROBABILISTIC_CATEGORIES:
            return False
        if claim.category != Category.EMPIRICAL:
            return False
        if claim.modality.value not in EpistemicIdentity.EVIDENCE_MODALITIES:
            return False
        if claim.predicate not in EpistemicIdentity.CAUSAL_EMPIRICAL_PREDICATES:
            return False
        return True

    @staticmethod
    def is_complete(
        claim: "ClaimNode",
        patch_chain: "Any",  # PatchChain — avoid circular import
    ) -> tuple[bool, list[str]]:
        """
        Check whether a claim's epistemic identity is complete:
        - Has non-empty lineage OR is a fresh claim (lineage may be empty on first ADD)
        - All lineage patch_ids are present in the chain
        - If uncertainty_required() → uncertainty is set

        Returns (complete, list_of_issues).
        """
        issues = []

        # Uncertainty check
        if EpistemicIdentity.uncertainty_required(claim) and claim.uncertainty is None:
            issues.append(
                f"uncertainty required for EMPIRICAL/{claim.modality.value}/"
                f"{claim.predicate} but not set"
            )

        # Assumptions check
        if not claim.assumptions:
            issues.append("assumptions[] is empty — protocol violation (Section VII.13.2)")

        # Lineage vs patch chain consistency
        if claim.lineage and patch_chain is not None:
            chain_ids = {p.patch_id for p in patch_chain._patches}
            for pid in claim.lineage:
                if pid not in chain_ids:
                    issues.append(f"lineage patch {pid[:8]}… not found in PatchChain")

        return len(issues) == 0, issues
