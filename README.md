# Alexandria-Protokoll
Epistemic infrastructure for tamper-proof knowledge lineage — replacing citation authority with cryptographically auditable contribution chains.

# Alexandria Protocol — Reference Implementation (v0.1)

Minimal reference implementation of the **Alexandria Protocol**, a formally defined epistemic infrastructure for temporally anchored, manipulation-resistant knowledge management.

This code accompanies the working paper:

> Rentschler, H.-S. (2026). *The Alexandria Protocol: A Formally Defined Epistemic Infrastructure for Auditable Knowledge Lineage*. SSRN Working Paper. 

-----

## What is Alexandria?

Modern knowledge systems rely on authority, reputation, and citation chains to establish credibility. These mechanisms are opaque, retractable, and susceptible to manipulation.

Alexandria replaces this with **formally auditable epistemic lineage**: every knowledge claim is cryptographically anchored, every modification is traceable, and every dissent is preserved — permanently and tamper-detectably.

Alexandria does not determine truth. It enforces structural admissibility.

**Three guarantees:**

- Reconstructibility: any epistemic state can be rebuilt from its patch chain
- Tamper detectability: any modification of prior claims produces a hash mismatch
- Dissent preservation: disagreement creates branches, never deletions

-----

## What this implementation demonstrates

- Typed epistemic nodes (`EMPIRICAL`, `MODEL`, `NORMATIVE`, `SPECULATIVE`)
- Patch-DSL with operations `ADD`, `MODIFY`, `DEPRECATE`, `BRANCH`
- Structural audit gate (schema, category purity, temporal monotonicity, uncertainty disclosure)
- Append-only SHA-256 hash chain anchoring
- Branch formation for dissent handling
- Deterministic state reconstruction with integrity verification
- Stability as validation persistence (not truth probability)

**No external dependencies.** Standard Python 3.8+.

-----

## Quickstart

```bash
python alexandria_core.py
```

Expected output:

```json
{
  "branch": "b_temp_constraint",
  "nodes": [
    {
      "id": "claim_001",
      "category": "EMPIRICAL",
      "deprecated": true,
      "stability": 0.0869,
      "sigma": 0.11,
      "assumptions": ["Temp_below_0C", "Measurement_Calibrated_v1"],
      "lineage_len": 3
    }
  ]
}
```

```
Reconstruction OK; tamper detection OK.
```

The demo runs a complete epistemic lifecycle: initial claim → branch with dissent → deprecation due to measurement artifact. Full history is preserved and reconstructible at every stage.

-----

## Deliberate simplifications

This is a pedagogical reference, not production code. The following simplifications are intentional and documented in the paper (Appendix D):

|Simplification                       |Production requirement                                   |
|-------------------------------------|---------------------------------------------------------|
|Discrete stability approximation     |Continuous exponentially weighted integral (Section XI.4)|
|Flat decay parameter                 |Domain-calibrated λ_k per knowledge element              |
|Partial audit gate                   |Full five-block audit (Section X)                        |
|No uncertainty propagation constraint|U(k₂) ≥ f(U(k₁)) per Section VII.6                       |
|In-memory storage                    |Persistent, distributed backend                          |

-----

## Relation to the Dual-Layer Economy

Alexandria and the [Dual-Layer Economy](https://ssrn.com) [link to be added] operate on complementary levels:

- Alexandria: epistemic architecture — how knowledge is assessed and attributed
- DLE: economic architecture — how value flows are kept stable within ecological limits

Knowledge validated through Alexandria can flow as a public good into the real economy layer of the DLE, with contribution attribution permanently preserved. This makes Alexandria a prerequisite for any future monetization of knowledge graphs that must exclude manipulation.

-----

## License

MIT License. Use freely, cite honestly.
