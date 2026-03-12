"""
Alexandria Core — Public API v1.0.0
Alexandria Protocol v2.1  |  Hanns-Steffen Rentschler
"""

# ── Core schema (no external deps) ───────────────────────────────────────────
from .schema import (
    ClaimNode, EntityNode, ConceptNode, WorkNode,
    AuthorNode, InstitutionNode, EvidenceNode, JudgmentNode, Patch,
    Category, Modality, EpistemicStatus, BuilderOrigin,
    PatchOperation, UncertaintyType, RelationType,
    Uncertainty, Validation,
    causal_priority, CAUSAL_SCALE_PRIORITY,
    BranchNode, BranchStatus, MergePolicy,
    EpistemicIdentity,
)
from .patch import PatchChain, PatchEmitter, compute_patch_hash
from .audit import (
    AuditGate, AuditReport, BlockResult,
    ThreeLevelAudit, PatchAuditResult, ClaimAuditResult, GraphAuditResult,
)
from .diff import (
    DiffEngine, DiffNode, DiffReport,
    DiffType, DiffSeverity, DiffStatus,
    BuilderBiasAnalyzer, DIFF_SEVERITY,
)
from .adjudication import (
    Adjudicator, AdjudicationResult, AdjudicationOutcome, RuleApplication,
)
from .seal import SealEngine, SealResult, SealRecord, CriterionResult
from .maturity import (
    MaturityCalculator, MaturityReport, MaturityLevel, MaturityTrend,
    MetricResult, maturity_level, MATURITY_THRESHOLDS,
)

# ── Optional: require httpx ───────────────────────────────────────────────────
from .relations import RelationsMatrix, AdmissibilityResult, CAUSAL_PREDICATES, HARD_CAUSAL_PREDICATES

# ── Optional: require httpx ───────────────────────────────────────────────────
try:
    from .builder import (
        Builder, BuilderConfig, DualBuilderPipeline,
        ClaimParser, WorkSource, ConceptSource,
        MappingConfidence, ConceptMappingResult,
    )
    from .sources import OpenAlexClient, OpenCycLoader
    from .pipeline import AlexandriaPipeline, PipelineResult
    _builder_available = True
except ImportError:
    _builder_available = False

# ── Optional: require neo4j ───────────────────────────────────────────────────
try:
    from .db import AlexandriaDB
    _db_available = True
except ImportError:
    _db_available = False

# ── SPL Interface (Semantic Projection Layer — WP2) ───────────────────────────
from .spl import (
    SemanticUnit,
    SemanticProjection,
    ClaimCandidate,
    EmissionStatus,
    EmissionRule,
    SPLThresholds,
    EmissionEngine,
    ClaimCandidateConverter,
    compute_jsd,
    compute_h_norm,
)

__version__  = "1.1.0"
__protocol__ = "Alexandria Protocol v2.2 + SPL (WP2)"
__author__   = "Hanns-Steffen Rentschler"

def status():
    """Print availability of optional dependencies."""
    print(f"Alexandria Core {__version__}  ({__protocol__})")
    print(f"  Core (schema, patch, audit, diff, adjudication, seal, maturity): OK")
    print(f"  SPL interface (spl.py — WP2 boundary layer):                     OK")
    print(f"  Builder / Pipeline (requires httpx):  {'OK' if _builder_available else 'NOT INSTALLED  pip install httpx'}")
    print(f"  Database (requires neo4j):             {'OK' if _db_available else 'NOT INSTALLED  pip install neo4j'}")
