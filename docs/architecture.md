# Alexandria Protocol — Architecture

## Overview

Alexandria is an epistemic infrastructure protocol for building auditable
knowledge graphs from scientific literature. It does not determine truth;
it enforces *structural admissibility* through cryptographic anchoring,
mandatory uncertainty disclosure, and formal dissent preservation.

---

## Layer Model

```
┌─────────────────────────────────────────────────────────────────┐
│  Raw Sources                                                    │
│  OpenAlex (scholarly literature)  ·  OpenCyc (ontology)        │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Semantic Projection Layer  (external — spl.py / WP2)          │
│                                                                 │
│  text → SemanticUnit → SemanticProjection → ClaimCandidate      │
│                                                                 │
│  [SHALL] No text fragment may bypass the SPL and become a      │
│          ClaimNode directly. The SPL gateway is the only       │
│          legal entry point into the protocol.                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │  ClaimCandidate  (boundary)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Alexandria Protocol                                            │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐                            │
│  │ Builder Alpha│  │ Builder Beta │   (isolated, parallel)     │
│  └──────┬───────┘  └───────┬──────┘                            │
│         └────────┬─────────┘                                   │
│                  ▼                                              │
│          ┌───────────────┐                                      │
│          │  Diff Engine  │  25 typed DiffNode classes          │
│          └───────┬───────┘                                      │
│                  ▼                                              │
│          ┌───────────────┐                                      │
│          │  Adjudicator  │  Rules C.1–C.9, BranchNode trigger  │
│          └───────┬───────┘                                      │
│                  ▼                                              │
│          ┌───────────────┐                                      │
│          │  Audit Gate   │  5 blocks (Patch / Claim / Graph)   │
│          └───────┬───────┘                                      │
│                  ▼                                              │
│          ┌───────────────┐                                      │
│          │ Patch Emitter │  SHA-256 append-only chain          │
│          └───────┬───────┘                                      │
│                  ▼                                              │
│          ┌───────────────┐                                      │
│          │  Seal Engine  │  Hard criteria D.1–D.4, D.6         │
│          └───────┬───────┘                                      │
│                  ▼                                              │
│          ┌───────────────┐                                      │
│          │   Maturity    │  M1–M5, composite score Φ           │
│          └───────┬───────┘                                      │
└──────────────────┼──────────────────────────────────────────────┘
                   │  ClaimNode (SEALED)
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│  Epistemic Graph  (Neo4j)                                       │
│  ClaimNodes · EntityNodes · DiffNodes · BranchNodes · Patches  │
└─────────────────────────────────────────────────────────────────┘
```

---

## SPL Boundary

```
ClaimCandidate
     ↓
SPL Gateway  (external — Alexandria-Semantic-Projection-Layer)
     ↓
ClaimNode
```

`ClaimCandidate` objects are produced by the **Semantic Projection Layer**
(a separate repository). The SPL gateway performs:

- Boundary validation (schema conformance)
- Confidence scoring
- Ontology alignment (OpenCyc mapping)

Only gateway-validated `ClaimCandidates` may enter the protocol as
`ClaimNodes`. This separation ensures the protocol layer never processes
raw text.

---

## Pipeline Steps

| Step | Module | Description |
|------|--------|-------------|
| **Sources** | `sources.py` | OpenAlex REST client; OpenCyc loader |
| **Builder Alpha/Beta** | `builder.py` | Independent graph construction (LLM or rule-based) |
| **Diff Engine** | `diff.py` | Typed divergence detection between G_α and G_β (25 DiffNode types) |
| **Adjudicator** | `adjudication.py` | Rules C.1–C.9; unresolvable diffs trigger BranchNodes |
| **Audit Gate** | `audit.py` | Three-level structural audit (Patch · Claim · Graph) |
| **Patch Emitter** | `patch.py` | SHA-256 hash chain; append-only; tamper-evident |
| **Seal Engine** | `seal.py` | Immutable snapshot; promotes VALIDATED → SEALED |
| **Maturity Calculator** | `maturity.py` | Composite score Φ (M1–M5); maturity level thresholds |
| **Neo4j Adapter** | `db.py` | Persistence layer; schema constraints; CRUD |
| **SPL Interface** | `spl.py` | Boundary between Semantic Projection Layer and protocol |

---

## Norm Levels

| Marker | Meaning |
|--------|---------|
| `[SHALL]` | Protocol invariant — violation = `FORMAL_ERROR` |
| `[DBA]` | Dual-Builder Architecture extension |
| `[HEURISTIC]` | Reference default — replaceable in production |
| `[ADVISORY]` | Recommendation — no formal status |

---

## Hard Seal Criteria

| ID | Criterion |
|----|-----------|
| D.1 | No open HIGH-severity DiffNodes |
| D.2 | Every claim has ≥ 1 `source_ref` or `evidence_ref` |
| D.3 | No claim with `status = FORMAL_ERROR` |
| D.4 | SHA-256 patch chain integrity verified |
| D.5 | *(Advisory — Maturity Φ logged, never blocks seal)* |
| D.6 | Every claim has non-empty `assumptions[]` |

---

## Exception Hierarchy

```
AlexandriaError
├── SchemaError
│   └── RelationAdmissibilityError
├── ValidationError
│   ├── UncertaintyRequiredError
│   └── AssumptionsMissingError
├── PatchChainError
│   └── IntegrityError
├── PersistenceError
│   ├── ConnectionError
│   └── WriteError
├── AuditError
└── BuilderError
    └── LLMResponseError
```

Defined in `alexandria_core/exceptions.py`.

---

## Related Repositories

- **Alexandria Protocol** (this repo) — protocol + reference implementation
- **[Alexandria Semantic Projection Layer](https://github.com/hstre/Alexandria-Semantic-Projection-Layer)** — SPL gateway (WP2)
