"""
Alexandria Core — pipeline.py
End-to-end DBA Pipeline Orchestrator

Connects all layers in sequence:

    Source (OpenAlex / OpenCyc)
        ↓
    DualBuilderPipeline  (Alpha + Beta, isolated)
        ↓
    DiffEngine           (typed DiffNodes → DiffReport)
        ↓
    Adjudicator          (Annex C rules → AdjudicationResult)
        ↓
    AuditGate            (5 blocks per resolved claim)
        ↓
    PatchEmitter         (SHA-256 chain)
        ↓
    SealEngine           (Annex D criteria → SealRecord)
        ↓
    MaturityCalculator   (Annex G metrics → MaturityReport)

Optional Neo4j persistence at each stage.

Usage (minimal, no Neo4j):

    pipeline = AlexandriaPipeline()
    result   = pipeline.run_work(work_source)
    print(result.summary())

Usage (with Neo4j):

    from alexandria_core.db import AlexandriaDB
    with AlexandriaDB(password="...") as db:
        db.deploy_schema()
        pipeline = AlexandriaPipeline(db=db)
        result   = pipeline.run_work(work_source)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from .schema import ClaimNode, EpistemicStatus
from .builder import DualBuilderPipeline, BuilderConfig, WorkSource, ConceptSource
from .diff import DiffEngine, DiffReport
from .adjudication import Adjudicator, AdjudicationResult
from .audit import AuditGate, ThreeLevelAudit, GraphAuditResult
from .patch import PatchChain, PatchEmitter
from .seal import SealEngine, SealResult
from .maturity import MaturityCalculator, MaturityReport, MaturityTrend

log = logging.getLogger(__name__)


# ── Pipeline result ───────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    """
    Complete result of one pipeline run (one source document).
    """
    source_ref:        str
    elapsed:           float

    # Per-layer outputs
    claims_alpha:      list[ClaimNode]
    claims_beta:       list[ClaimNode]
    diff_report:       DiffReport
    adj_result:        AdjudicationResult
    resolved_claims:   list[ClaimNode]    # after audit gate
    audit_passed:      int
    audit_failed:      int

    # Chain state after this run
    patch_count:       int
    chain_head_hash:   str

    # Optional: populated if seal attempted
    seal_result:       Optional[SealResult] = None
    maturity:          Optional[MaturityReport] = None
    graph_audit:       Optional[GraphAuditResult] = None   # Sprint 4

    def summary(self) -> str:
        lines = [
            f"PipelineResult — source={self.source_ref}",
            f"  Elapsed:         {self.elapsed:.1f}s",
            f"  Alpha claims:    {len(self.claims_alpha)}",
            f"  Beta claims:     {len(self.claims_beta)}",
            f"  Diffs:           {len(self.diff_report.diffs)} "
            f"(H={len(self.diff_report.high)} "
            f"M={len(self.diff_report.medium)} "
            f"L={len(self.diff_report.low)})",
            f"  Adj. resolved:   {len(self.adj_result.resolved_claims)}",
            f"  Adj. branches:   {self.adj_result.branch_count}",
            f"  Adj. errors:     {len(self.adj_result.formal_errors)}",
            f"  Audit passed:    {self.audit_passed}",
            f"  Audit failed:    {self.audit_failed}",
            f"  Patch chain:     {self.patch_count} patches",
            f"  Chain head:      {self.chain_head_hash[:16]}…",
        ]
        if self.graph_audit:
            status = "OK" if self.graph_audit.passed else f"{len(self.graph_audit.violations)} issues"
            lines.append(f"  Graph audit:     {status}")
        if self.maturity:
            lines.append(
                f"  Maturity Φ:      {self.maturity.phi:.4f} "
                f"({self.maturity.level.value})"
            )
        if self.seal_result:
            lines.append(
                f"  Seal:            "
                f"{'SUCCESS' if self.seal_result.success else 'REJECTED'} "
                f"({self.seal_result.sealed_count} claims sealed)"
            )
        return "\n".join(lines)


# ── Pipeline ──────────────────────────────────────────────────────────────────

class AlexandriaPipeline:
    """
    End-to-end DBA pipeline.

    State is accumulated across multiple run_work() / run_concept() calls.
    The patch chain, claim accumulator, and diff history persist between runs.
    Call seal() explicitly when ready.

    Parameters:
        config_alpha:  BuilderConfig for Builder Alpha (default: localhost:1234)
        config_beta:   BuilderConfig for Builder Beta  (default: localhost:1234)
        db:            Optional AlexandriaDB — if given, all nodes are persisted
        graph_id:      Graph identifier for seal records
    """

    def __init__(
        self,
        config_alpha: BuilderConfig | None = None,
        config_beta:  BuilderConfig | None = None,
        db=None,
        graph_id: str = "alexandria",
    ):
        self.graph_id = graph_id
        self.db       = db

        # Core components
        self._builder  = DualBuilderPipeline(config_alpha, config_beta)
        self._diff     = DiffEngine()
        self._audit    = AuditGate()
        self._three    = ThreeLevelAudit()   # Sprint 4: graph-level audit
        self._seal     = SealEngine()
        self._maturity = MaturityCalculator()
        self._trend    = MaturityTrend()

        # Accumulate state across runs
        self._chain:        PatchChain      = PatchChain()
        self._emitter:      PatchEmitter    = PatchEmitter(self._chain)
        self._all_claims:   list[ClaimNode] = []
        self._diff_reports: list[DiffReport] = []
        self._branches:     list            = []   # BranchNode list (v2.2)
        self._alpha_claims: dict[str, ClaimNode] = {}
        self._beta_claims:  dict[str, ClaimNode] = {}
        self._seal_version: int             = 1

    # ── Public API ────────────────────────────────────────────────────────────

    def run_work(
        self,
        work:          WorkSource,
        attempt_seal:  bool = False,
    ) -> PipelineResult:
        """
        Process one scientific work through the full pipeline.
        Accumulates claims and diffs in internal state.
        """
        return self._run(
            source_ref    = work.openalex_id or work.doi or work.title[:40],
            get_claims_fn = lambda: self._builder.process_work(work),
            attempt_seal  = attempt_seal,
        )

    def run_concept(
        self,
        concept:       ConceptSource,
        attempt_seal:  bool = False,
    ) -> PipelineResult:
        """
        Process one ontological concept through the full pipeline.
        """
        return self._run(
            source_ref    = f"cyc:{concept.cyc_id or concept.name}",
            get_claims_fn = lambda: self._builder.process_concept(concept),
            attempt_seal  = attempt_seal,
        )

    def seal(self, force: bool = False) -> SealResult:
        """
        Attempt to seal the accumulated graph.
        Call after one or more run_work() / run_concept() calls.
        """
        result = self._seal.seal(
            claims       = self._all_claims,
            diff_reports = self._diff_reports,
            patch_chain  = self._chain,
            emitter      = self._emitter,
            graph_id     = self.graph_id,
            version      = self._seal_version,
            force        = force,
        )
        if result.success:
            self._seal_version += 1
            if result.seal_record and self.db:
                try:
                    self.db.run_cypher(
                        "MERGE (s:Seal {seal_id: $sid}) SET s += $props",
                        sid=result.seal_record.seal_id,
                        props=result.seal_record.to_dict(),
                    )
                except Exception as e:
                    log.error(f"Failed to persist SealRecord: {e}")
        return result

    def maturity_report(self) -> MaturityReport:
        """Compute current maturity of the accumulated graph."""
        report = self._maturity.assess(
            claims       = self._all_claims,
            diff_reports = self._diff_reports,
            patch_chain  = self._chain,
            graph_id     = self.graph_id,
        )
        self._trend.add(report)
        return report

    @property
    def claim_count(self) -> int:
        return len(self._all_claims)

    @property
    def patch_count(self) -> int:
        return self._chain.length

    @property
    def trend(self) -> MaturityTrend:
        return self._trend

    # ── Internal pipeline ─────────────────────────────────────────────────────

    def _run(
        self,
        source_ref:    str,
        get_claims_fn,
        attempt_seal:  bool,
    ) -> PipelineResult:
        t0 = time.time()
        log.info(f"Pipeline: processing {source_ref!r}")

        # ── 1. Build ──────────────────────────────────────────────────────────
        claims_alpha, claims_beta = get_claims_fn()
        # Index claims for branch resolution (v2.2)
        self._alpha_claims.update({c.claim_id: c for c in claims_alpha})
        self._beta_claims.update({c.claim_id: c for c in claims_beta})

        # ── 2. Diff ───────────────────────────────────────────────────────────
        diff_report = self._diff.compare(claims_alpha, claims_beta, source_ref)
        self._diff_reports.append(diff_report)

        # ── 3. Adjudicate ─────────────────────────────────────────────────────
        adjudicator = Adjudicator(claims_alpha, claims_beta)
        adj_result  = adjudicator.adjudicate(diff_report)

        # ── 4. Audit + patch ──────────────────────────────────────────────────
        audit_passed = 0
        audit_failed = 0
        new_claims: list[ClaimNode] = []

        prior_patches = list(self._chain._patches)

        for claim in adj_result.resolved_claims:
            try:
                patch = self._emitter.add(claim)
                report = self._audit.audit(
                    patch, claim, prior_patches=prior_patches
                )
                claim.status = report.final_status
                if report.passed:
                    audit_passed += 1
                else:
                    audit_failed += 1
                    log.warning(
                        f"Audit failed for {claim.claim_id[:8]}…: "
                        f"{[b.errors for b in report.failed_blocks]}"
                    )
                new_claims.append(claim)
                prior_patches = list(self._chain._patches)
            except ValueError as e:
                log.error(f"Patch error for {claim.claim_id[:8]}…: {e}")
                audit_failed += 1

        # Branch triggers — BranchNode first-class objects (v2.2)
        for branch in adj_result.branch_triggers:
            try:
                ca_id = branch.claim_alpha_id
                cb_id = branch.claim_beta_id
                ca = self._alpha_claims.get(ca_id) or self._beta_claims.get(ca_id)
                cb = self._alpha_claims.get(cb_id) or self._beta_claims.get(cb_id)
                if ca:
                    self._emitter.branch(ca, branch_id=branch.branch_id)
                    new_claims.append(ca)
                if cb:
                    self._emitter.branch(cb, branch_id=branch.branch_id)
                    new_claims.append(cb)
                self._branches.append(branch)
                log.info(
                    f"Branch recorded: {branch.branch_id[:8]}... "
                    f"reason={branch.branch_reason[:60]} "
                    f"policy={branch.merge_policy.value}"
                )
            except Exception as e:
                log.error(f"Branch patch error: {e}")

        # ── 5. Persist to Neo4j (if db available) ─────────────────────────────
        if self.db:
            self._persist(
                claims_alpha, claims_beta,
                new_claims, diff_report,
                adj_result,
            )

        # ── 6. Accumulate ─────────────────────────────────────────────────────
        self._all_claims.extend(new_claims)

        # ── 6b. Graph Audit (ThreeLevelAudit Level 3) — Sprint 4 ─────────────
        graph_audit = self._three.audit_graph(
            claims    = self._all_claims,
            branches  = self._branches,
            patch_chain = self._chain,
            graph_id  = self.graph_id,
        )
        if not graph_audit.passed:
            log.warning(
                f"Graph audit: {len(graph_audit.violations)} issue(s) — "
                f"orphans={len(graph_audit.orphan_claim_ids)} "
                f"branches={len(graph_audit.unresolved_branches)} "
                f"source_gaps={len(graph_audit.source_gaps)}"
            )

        # ── 7. Optional seal ──────────────────────────────────────────────────
        seal_result = None
        maturity    = None
        if attempt_seal:
            maturity    = self.maturity_report()
            seal_result = self.seal()

        elapsed = time.time() - t0
        log.info(
            f"Pipeline: {source_ref!r} done in {elapsed:.1f}s — "
            f"{len(new_claims)} claims, "
            f"{len(diff_report.diffs)} diffs, "
            f"{audit_passed} audits passed"
        )

        return PipelineResult(
            source_ref      = source_ref,
            elapsed         = elapsed,
            claims_alpha    = claims_alpha,
            claims_beta     = claims_beta,
            diff_report     = diff_report,
            adj_result      = adj_result,
            resolved_claims = new_claims,
            audit_passed    = audit_passed,
            audit_failed    = audit_failed,
            patch_count     = self._chain.length,
            chain_head_hash = self._chain.head_hash,
            seal_result     = seal_result,
            maturity        = maturity,
            graph_audit     = graph_audit,
        )

    def _persist(
        self,
        claims_alpha:  list[ClaimNode],
        claims_beta:   list[ClaimNode],
        resolved:      list[ClaimNode],
        diff_report:   DiffReport,
        adj_result:    AdjudicationResult,
    ):
        """Persist all new nodes to Neo4j."""
        db = self.db
        try:
            for c in claims_alpha + claims_beta + resolved:
                db.upsert_claim(c)
            for diff in diff_report.diffs:
                db.run_cypher(
                    "MERGE (d:Diff {diff_id: $did}) SET d += $props",
                    did=diff.diff_id, props=diff.to_dict(),
                )
            for j in adj_result.judgments:
                db.upsert_judgment(j)
                db.link_judgment_to_claims(
                    j.judgment_id, j.claim_alpha_id, j.claim_beta_id
                )
            for patch in self._chain._patches:
                db.store_patch(patch)
        except Exception as e:
            log.error(f"Persistence error: {e}")
