# Alexandria Protocol

**Epistemic Infrastructure for Validated Knowledge Graphs**

> *A formally defined protocol that treats knowledge as a temporally indexed, structurally reconstructible process — not a static set of conclusions.*

**Hanns-Steffen Rentschler · 2026**

---

## What is Alexandria?

Alexandria is a protocol and reference implementation for building **epistemically auditable knowledge graphs**. It does not determine truth. Instead, it enforces structural admissibility through:

- Explicit derivation paths and source traceability
- Cryptographically anchored, append-only patch chains
- Mandatory uncertainty disclosure for empirical claims
- Dissent preservation via formal branch management (no silent winners)
- A three-level audit gate (Patch / Claim / Graph)

The protocol is built on two independent knowledge sources: **OpenCyc** (ontology) and **OpenAlex** (scholarly literature). Two independent builders (Alpha, Beta) construct graphs in parallel; a Diff Engine and Adjudication Rulebook handle disagreements explicitly.

---

## Current Version: v2.2

### What changed in v2.2 (Sprint 1–3)

| # | Component | Change |
|---|-----------|--------|
| 1 | Norm levels | Explicit three-level separation: `[SHALL]` / `[DBA]` / `[HEURISTIC]` |
| 2 | Adjudication C.3 | No silent Alpha default — unknown diff types → `UNRESOLVED_PENDING_RULE` |
| 3 | BranchNode | First-class object with full lifecycle schema |
| 4 | Seal D.5 | Maturity (Φ) is advisory only — never blocks a formally correct seal |
| 5 | EpistemicIdentity | Claim + Lineage + Patch History as normative primary unit |
| 6 | RelationsMatrix | Machine-checkable admissibility enforcement (Category × Predicate) |
| 7 | ThreeLevelAudit | Patch / Claim / Graph audit levels cleanly separated |
| 8 | Uncertainty rule | Precise 3-condition logic via `EpistemicIdentity.uncertainty_required()` |
| 9 | MappingConfidence | Ontology mapping with explicit confidence tier |
| 10 | DiffNode bias metadata | `adjudication_rule`, `winning_builder`, `bias_tag` |
| 11 | SPL interface layer | Semantic Projection Layer — legal path from text to ClaimNode (WP2) |
| 12 | Evaluation framework | Benchmark plan for Precision/Recall, calibration, mapping quality |

---

## Repository Structure

```
alexandria_core/          # Reference implementation (Python)
├── schema.py             # ClaimNode, BranchNode, EpistemicIdentity, all enums
├── adjudication.py       # Rules C.1–C.9, BranchNode trigger
├── audit.py              # AuditGate (5 blocks) + ThreeLevelAudit
├── diff.py               # DiffNode (25 types), DiffEngine, BuilderBiasAnalyzer
├── seal.py               # SealEngine, hard criteria D.1–D.4, D.6
├── maturity.py           # M1–M5, Composite Φ, MaturityLevel
├── patch.py              # PatchChain (SHA-256), PatchEmitter
├── relations.py          # RelationsMatrix, AdmissibilityResult
├── builder.py            # Builder Alpha/Beta, MappingConfidence
├── sources.py            # OpenAlexClient, OpenCycLoader
├── pipeline.py           # AlexandriaPipeline, PipelineResult
├── db.py                 # Neo4j adapter, schema deployment
├── spl.py                # Semantic Projection Layer (WP2)
└── __init__.py           # Public API v1.1.0
```

---

## Architecture

```
Layer 0   Raw Sources       OpenCyc + OpenAlex
Layer 1   SemanticUnit      text segmentation
Layer 2   SemanticProjection  concept alignment (WP2 / spl.py)
Layer 3   ClaimCandidate    scored candidates (WP2 / spl.py)
Layer 4   Canonical Claim   protocol boundary (ClaimCandidateConverter)
Layer 5   Alexandria Protocol  Diff · Adjudication · Branch · Seal
Layer 6   Epistemic Graph   Neo4j
Layer 7   Synapse           cross-actor claim-graph similarity
```

**Protocol invariant [SHALL]:** No text fragment may become a ClaimNode directly.  
Only legal path: `text → SemanticUnit → SemanticProjection → ClaimCandidate → ClaimNode`

---

## Norm Levels

| Marker | Meaning |
|--------|---------|
| `[SHALL]` | Protocol invariant — never deviate; violation = FORMAL_ERROR |
| `[DBA]` | Dual-Builder Architecture extension — protocol-compliant, explicitly declared |
| `[HEURISTIC]` | Reference implementation default — replaceable |
| `[ADVISORY]` | Recommendation — no formal status |

---

## Hard Seal Criteria

| Criterion | Condition |
|-----------|-----------|
| D.1 | No open HIGH-severity DiffNodes |
| D.2 | Every claim has ≥1 source_ref or evidence_ref |
| D.3 | No claim with status=FORMAL_ERROR |
| D.4 | SHA-256 patch chain integrity verified |
| D.5 | *(Advisory only — Maturity Φ logged, never blocks seal)* |
| D.6 | Every claim has non-empty assumptions[] |

---

## Dependencies

```
neo4j>=5.0.0
httpx
```

No NLP backend required for the protocol layer. The SPL (spl.py) is designed for integration with sentence-transformers or similar embedding models.

---

## Related Papers (SSRN)

- **Alexandria Protocol v2.2** — this repository  
- **Working Paper 2: Semantic Projection Layer** — SPL interface specification (spl.py)  
- **Dual-Layer Economy (DLE)** — SSRN 5885342  
- **PES: Persistent Epistemic Supervisor** — forthcoming  

---

## License

Code: MIT  
Protocol specification: CC BY 4.0

© 2026 Hanns-Steffen Rentschler
