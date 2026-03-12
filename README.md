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
Alexandria-Protokoll/
│
├── alexandria_core/              # Protocol implementation (Python package)
│   ├── __init__.py               # Public API v1.1.0
│   ├── schema.py                 # ClaimNode, BranchNode, EpistemicIdentity, all enums
│   ├── adjudication.py           # Rules C.1–C.9, BranchNode trigger
│   ├── audit.py                  # AuditGate (5 blocks) + ThreeLevelAudit
│   ├── diff.py                   # DiffNode (25 types), DiffEngine, BuilderBiasAnalyzer
│   ├── seal.py                   # SealEngine, hard criteria D.1–D.4, D.6
│   ├── maturity.py               # M1–M5, composite Φ, MaturityLevel
│   ├── patch.py                  # PatchChain (SHA-256), PatchEmitter
│   ├── relations.py              # RelationsMatrix, AdmissibilityResult
│   ├── builder.py                # Builder Alpha/Beta, MappingConfidence
│   ├── sources.py                # OpenAlexClient, OpenCycLoader
│   ├── pipeline.py               # AlexandriaPipeline, PipelineResult
│   ├── db.py                     # Neo4j adapter, schema constraints
│   ├── spl.py                    # Semantic Projection Layer interface (WP2)
│   └── exceptions.py             # Protocol-specific exception hierarchy
│
├── examples/
│   └── demo_pipeline.py          # Offline demo (no API key required)
│
├── tests/
│   ├── test_schema.py
│   ├── test_patch.py
│   ├── test_diff.py
│   └── test_pipeline.py
│
├── docs/
│   ├── architecture.md           # Layer model, SPL boundary, pipeline steps
│   ├── reference_v0.1.py         # Pedagogical reference implementation (v0.1)
│   ├── Alexandria Protocol Complete v2.2 merged.pdf
│   └── Alexandria Protocol v2.pdf
│
├── openalex_ingest.py            # CLI: OpenAlex → claims (rule-based or LLM)
├── alexandria_dashboard.py       # Streamlit visual dashboard
├── CHANGELOG.md
└── LICENSE
```

---

## Quick Start

### Offline demo (no API key)

```bash
python examples/demo_pipeline.py
```

### OpenAlex ingest (rule-based, no LLM)

```bash
python openalex_ingest.py "mRNA vaccines" --max 10 --email you@example.com
```

### OpenAlex ingest with LLM (single builder)

```bash
python openalex_ingest.py "mRNA vaccines" --max 5 \
    --llm-key $DEEPSEEK_API_KEY
```

### Full dual-builder pipeline

```bash
python openalex_ingest.py "mRNA vaccines" --max 5 \
    --llm-key  $DEEPSEEK_API_KEY \
    --llm-key-b $OPENROUTER_API_KEY
```

### Visual dashboard

```bash
pip install streamlit plotly networkx
streamlit run alexandria_dashboard.py
```

### Tests

```bash
pip install pytest
python -m pytest tests/
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

`ClaimCandidates` are produced by the **Semantic Projection Layer** (a separate repository). The SPL gateway performs boundary validation before `ClaimNodes` enter the protocol.

**Protocol invariant [SHALL]:** No text fragment may become a `ClaimNode` directly.
Only legal path: `text → SemanticUnit → SemanticProjection → ClaimCandidate → ClaimNode`

---

## Pipeline

```
Sources  (OpenAlex / OpenCyc)
     ↓
Dual Builder  (Alpha + Beta, isolated)
     ↓
Diff Engine   (25 typed DiffNode classes)
     ↓
Adjudication  (Rules C.1–C.9; unresolvable → BranchNode)
     ↓
Audit Gate    (Patch · Claim · Graph, three levels)
     ↓
Patch Emitter (SHA-256 append-only chain)
     ↓
Seal Engine   (Hard criteria D.1–D.4, D.6)
     ↓
Maturity      (M1–M5, composite Φ, level thresholds)
     ↓
Epistemic Graph  (Neo4j)
```

See [`docs/architecture.md`](docs/architecture.md) for the full layer diagram and step descriptions.

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
| D.2 | Every claim has ≥ 1 source_ref or evidence_ref |
| D.3 | No claim with status = FORMAL_ERROR |
| D.4 | SHA-256 patch chain integrity verified |
| D.5 | *(Advisory only — Maturity Φ logged, never blocks seal)* |
| D.6 | Every claim has non-empty assumptions[] |

---

## Dependencies

```
neo4j>=5.0.0
httpx
streamlit        # dashboard only
plotly           # dashboard only
networkx         # dashboard only
```

No NLP backend required for the protocol layer. The SPL (`spl.py`) is designed for integration with sentence-transformers or similar embedding models.

---

## Related Repositories

- **[Alexandria Semantic Projection Layer](https://github.com/hstre/Alexandria-Semantic-Projection-Layer)** — SPL gateway (WP2): text → ClaimCandidate

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
