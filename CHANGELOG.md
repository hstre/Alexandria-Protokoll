# Changelog

## v1.1.0 — Alexandria Protocol v2.2 (March 2026)

### Sprint 1 — Four Structural Fixes

- **Norm levels** (`schema.py`, all modules): Explicit three-level separation introduced. Every protocol statement now carries `[SHALL]`, `[DBA]`, `[HEURISTIC]`, or `[ADVISORY]`.
- **Adjudication C.3** (`adjudication.py`): Removed silent Alpha default. Unknown diff types now produce `UNRESOLVED_PENDING_RULE` with `winning_id = None`. C.3 is an escalation rule, not a resolution rule.
- **BranchNode** (`schema.py`, `adjudication.py`, `db.py`): Promoted to first-class object with full schema: `branch_id`, `parent_branch_id`, `trigger_diff_ids`, `branch_reason`, `merge_policy`, `status` lifecycle (OPEN / MERGED / DEPRECATED / ARCHIVED).
- **Seal D.5** (`seal.py`): Maturity score (Φ) is now `[ADVISORY]` only. `passed = True` always. Φ is logged in SealRecord for operational use but never blocks a formally correct seal.

### Sprint 2 — Four Formalizations

- **EpistemicIdentity doctrine** (`schema.py`): Epistemic primary unit formally defined as Claim + Lineage + Patch History. `EpistemicIdentity.is_complete()` checks all three.
- **RelationsMatrix** (`relations.py`): Machine-checkable admissibility matrix (Category × Predicate). Forbidden combinations → `FORMAL_ERROR`. Integrated into `AuditGate Block I` and Adjudicator C.8.
- **ThreeLevelAudit** (`audit.py`): Patch / Claim / Graph audit levels separated into distinct result types. Additive to existing AuditGate (5-block system retained).
- **Uncertainty enforcement** (`schema.py`, `audit.py`): `EpistemicIdentity.uncertainty_required()` replaces modality heuristic. Rule: EMPIRICAL + {evidence|established} + CAUSAL_EMPIRICAL_PREDICATE → uncertainty mandatory.

### Sprint 3 — Four Refinements

- **MappingConfidence** (`builder.py`, `sources.py`): Ontology mapping now returns `ConceptMappingResult` with explicit confidence tier (MAPPED / CANDIDATE / LOW_CONFIDENCE / MULTIPLE_CANDIDATES / UNMAPPED / EXCLUDED). `is_usable = False` → staging queue, no silent graph entry.
- **DiffNode bias metadata** (`diff.py`): Three optional fields added: `adjudication_rule`, `winning_builder`, `bias_tag`. `BuilderBiasAnalyzer` aggregates over all DiffNodes for systematic bias detection.
- **Uncertainty enforcement chain** (`schema.py`, `audit.py`): Full enforcement: `ClaimNode.validate()` → `PatchEmitter.add()` → `AuditGate Block V` → `ThreeLevelAudit.audit_claim()`.
- **Evaluation framework** (`pipeline.py`): Six measurement levels defined (Claim Extraction, Category Correctness, Predicate Correctness, Uncertainty Calibration, Diff Resolution Rate, Mapping Quality).

### SPL Interface Layer (WP2)

- **`spl.py`** (new): Full Semantic Projection Layer implementation. Legal path from text to ClaimNode enforced as protocol invariant. Emission rules E0–E4. Thresholds Θ = {τ₀=0.50, τ₁=0.60, τ₂=0.25, τ₃=0.65, τ₄=0.40}.

### Bug Fix

- **`relations.py`** `validate_claim()`: Removed redundant `uncertainty_required` check. Uncertainty enforcement is the exclusive responsibility of `EpistemicIdentity.uncertainty_required()`. The old check incorrectly flagged valid `EMPIRICAL/hypothesis/CAUSES` claims as `FORMAL_ERROR`.

---

## v1.0.0 — Alexandria Protocol v2.1 (initial release)

- Core schema: ClaimNode, Patch, PatchChain, AuditGate
- Dual-Builder Architecture (Alpha / Beta)
- Diff Engine (25 diff types)
- Adjudication Rulebook C.1–C.9
- Seal Policy D.1–D.9
- Graph Maturity Metrics M1–M5, Composite Φ
- Neo4j adapter
- OpenAlex + OpenCyc source integration
