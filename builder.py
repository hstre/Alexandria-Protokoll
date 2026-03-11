"""
Alexandria Core — builder.py
Builder Alpha / Beta Pipeline (Section V-A.5)

Each Builder receives source material (Work or Concept),
calls an LM Studio / OpenAI-compatible backend, extracts
structured Claims, validates them against the schema,
and returns ClaimNode instances tagged with builder_origin.

Isolation guarantee: Alpha and Beta share NO intermediate state.
They may run the same or different models on the same or different ports.
All configuration is explicit — nothing is implicit.

Requirements:
    pip install httpx

Environment variables (all optional, can also be passed directly):
    ALEXANDRIA_ALPHA_URL      default: http://localhost:1234/v1
    ALEXANDRIA_BETA_URL       default: http://localhost:1234/v1
    ALEXANDRIA_ALPHA_MODEL    default: local-model
    ALEXANDRIA_BETA_MODEL     default: local-model
    ALEXANDRIA_API_KEY        default: lm-studio  (LM Studio ignores this)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import httpx

from .schema import (
    BuilderOrigin, Category, ClaimNode, Modality,
    RelationType, Uncertainty, UncertaintyType, Validation,
)

log = logging.getLogger(__name__)


# ── Builder configuration ─────────────────────────────────────────────────────

@dataclass
class BuilderConfig:
    """
    All configuration for one Builder instance.
    Passed explicitly — no global state.
    """
    origin:      BuilderOrigin
    base_url:    str   = "http://localhost:1234/v1"
    model:       str   = "local-model"
    api_key:     str   = "lm-studio"
    temperature: float = 0.2        # low = more deterministic extraction
    max_tokens:  int   = 2048
    timeout:     float = 120.0      # seconds

    @classmethod
    def alpha(cls) -> "BuilderConfig":
        return cls(
            origin   = BuilderOrigin.ALPHA,
            base_url = os.environ.get("ALEXANDRIA_ALPHA_URL", "http://localhost:1234/v1"),
            model    = os.environ.get("ALEXANDRIA_ALPHA_MODEL", "local-model"),
            api_key  = os.environ.get("ALEXANDRIA_API_KEY", "lm-studio"),
        )

    @classmethod
    def beta(cls) -> "BuilderConfig":
        return cls(
            origin   = BuilderOrigin.BETA,
            base_url = os.environ.get("ALEXANDRIA_BETA_URL", "http://localhost:1234/v1"),
            model    = os.environ.get("ALEXANDRIA_BETA_MODEL", "local-model"),
            api_key  = os.environ.get("ALEXANDRIA_API_KEY", "lm-studio"),
        )


# ── Prompt templates ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an epistemic knowledge extractor for the Alexandria Protocol.
Your task: extract structured epistemic claims from source material.

STRICT OUTPUT FORMAT — respond ONLY with a JSON array of claim objects.
No preamble, no explanation, no markdown fences. Only the JSON array.

Each claim object must have these fields:
{
  "subject":      string,   // the entity or concept making the claim about
  "predicate":    string,   // relation type (see allowed list below)
  "object":       string,   // what the subject relates to
  "category":     string,   // one of: EMPIRICAL, NORMATIVE, MODEL, SPECULATIVE
  "modality":     string,   // one of: hypothesis, suggestion, evidence, established
  "qualifiers":   object,   // {key: value} scope qualifiers, can be empty {}
  "scope":        object,   // {population, region, domain}, can be empty {}
  "time_scope":   object,   // {start_year, end_year} if applicable, else {}
  "assumptions":  array,    // list of strings — MANDATORY, never empty
  "uncertainty":  object,   // {sigma, ci_low, ci_high, n} if empirical, else null
  "evidence_text": string   // brief quote or description from the source
}

ALLOWED predicates (choose the most precise one):
Causal scale: MENTIONS, RELATES_TO, CORRELATES_WITH, PARTIALLY_SUPPORTS,
              SUPPORTS, STRONGLY_SUPPORTS, CONTRIBUTES_TO, CAUSES
Claim-to-claim: CONTRADICTS, REFINES, DERIVED_FROM, EXTENDS
Evidence: HAS_EVIDENCE, SUPPORTED_BY, CONTRADICTED_BY

RULES:
1. assumptions[] must NEVER be empty. If unsure, list the scope constraints as assumptions.
2. Use CAUSES only if the source explicitly establishes causation with mechanism.
3. Use CORRELATES_WITH if the source reports correlation without causal claim.
4. NORMATIVE claims must not use CAUSES, CORRELATES_WITH, CONTRIBUTES_TO.
5. SPECULATIVE claims must not use CAUSES.
6. uncertainty is required when category=EMPIRICAL and modality is evidence or established.
7. Extract only what the source actually states — do not infer beyond the text.
8. If the source is too vague for a claim, return an empty array [].
"""

USER_PROMPT_WORK = """Extract epistemic claims from this scientific work.

SOURCE METADATA:
Title: {title}
Authors: {authors}
Year: {year}
Venue: {venue}
DOI: {doi}
OpenAlex ID: {openalex_id}

ABSTRACT / CONTENT:
{content}

Extract all distinct epistemic claims. For each claim, be precise about:
- What the study measured or asserted (subject/predicate/object)
- The epistemic strength (modality)
- The conditions under which it holds (scope, qualifiers, assumptions)
- Any quantitative uncertainty if reported

Return a JSON array. If no extractable claims, return [].
"""

USER_PROMPT_CONCEPT = """Extract epistemic claims from this ontological concept definition.

CONCEPT:
Name: {name}
Definition: {definition}
Broader concepts: {broader}
Narrower concepts: {narrower}

Extract the definitional and relational claims implied by this concept.
Focus on:
- IS-A relationships (INSTANCE_OF, SUBCLASS_OF)
- Necessary properties or conditions
- Typical relations to other concepts

Return a JSON array. If no extractable claims, return [].
"""


# ── Raw LLM output → ClaimNode ────────────────────────────────────────────────

class ClaimParser:
    """
    Parses raw JSON from the LLM into validated ClaimNode instances.
    Permissive on input, strict on output.
    """

    # Fallback assumptions when LLM returns none (should be rare)
    _FALLBACK_ASSUMPTIONS = ["SourceScope_AsStated", "ExtractedAutomatically"]

    def parse(
        self,
        raw:        list[dict],
        source_ref: str,
        origin:     BuilderOrigin,
    ) -> list[ClaimNode]:
        """
        Convert list of raw dicts (from LLM) into ClaimNodes.
        Invalid items are logged and skipped — not raised.
        """
        claims = []
        for i, item in enumerate(raw):
            try:
                claim = self._parse_one(item, source_ref, origin)
                errors = claim.validate()
                if errors:
                    log.warning(
                        f"Claim {i} from {source_ref} has validation issues "
                        f"(keeping as UNVALIDATED): {errors}"
                    )
                claims.append(claim)
            except Exception as e:
                log.error(f"Failed to parse claim {i} from {source_ref}: {e} | raw={item}")
        return claims

    def _parse_one(
        self,
        d:          dict,
        source_ref: str,
        origin:     BuilderOrigin,
    ) -> ClaimNode:
        # Category
        cat_raw = d.get("category", "EMPIRICAL").upper()
        try:
            category = Category(cat_raw)
        except ValueError:
            log.warning(f"Unknown category {cat_raw!r} — defaulting to SPECULATIVE")
            category = Category.SPECULATIVE

        # Modality
        mod_raw = d.get("modality", "hypothesis").lower()
        try:
            modality = Modality(mod_raw)
        except ValueError:
            modality = Modality.HYPOTHESIS

        # Assumptions — never empty
        assumptions = d.get("assumptions") or self._fallback_assumptions(d)
        if not assumptions:
            assumptions = self._FALLBACK_ASSUMPTIONS

        # Uncertainty
        uncertainty = None
        unc_raw = d.get("uncertainty")
        if unc_raw and isinstance(unc_raw, dict):
            try:
                uncertainty = Uncertainty(
                    sigma = float(unc_raw.get("sigma", 0.5)),
                    ci    = (
                        float(unc_raw.get("ci_low",  0.0)),
                        float(unc_raw.get("ci_high", 1.0)),
                    ),
                    n     = int(unc_raw.get("n", 1)),
                    type  = UncertaintyType.PROBABILISTIC,
                )
            except (TypeError, ValueError) as e:
                log.warning(f"Could not parse uncertainty {unc_raw}: {e}")

        # Evidence text → EvidenceNode reference (stored as source_ref for now)
        evidence_text = d.get("evidence_text", "")
        source_refs   = [source_ref]
        if evidence_text:
            source_refs.append(f"evidence:{evidence_text[:80]}")

        return ClaimNode.new(
            subject        = str(d.get("subject", "")),
            predicate      = str(d.get("predicate", "RELATES_TO")).upper(),
            object         = str(d.get("object", "")),
            category       = category,
            modality       = modality,
            qualifiers     = d.get("qualifiers") or {},
            scope          = d.get("scope") or {},
            time_scope     = d.get("time_scope") or {},
            assumptions    = assumptions,
            uncertainty    = uncertainty,
            source_refs    = source_refs,
            builder_origin = origin,
        )

    def _fallback_assumptions(self, d: dict) -> list[str]:
        """Construct minimal assumptions from scope if LLM left assumptions empty."""
        parts = []
        scope = d.get("scope") or {}
        for k, v in scope.items():
            if v:
                parts.append(f"Scope_{k.capitalize()}_{str(v).replace(' ', '_')[:30]}")
        ts = d.get("time_scope") or {}
        if ts.get("start_year") and ts.get("end_year"):
            parts.append(f"TemporalScope_{ts['start_year']}_{ts['end_year']}")
        return parts or self._FALLBACK_ASSUMPTIONS


# ── LLM client ────────────────────────────────────────────────────────────────

class LLMClient:
    """
    Thin wrapper around the OpenAI-compatible /v1/chat/completions endpoint.
    Works with LM Studio, Ollama (with OpenAI compat), and OpenAI itself.
    """

    def __init__(self, config: BuilderConfig):
        self.config = config
        self._client = httpx.Client(
            base_url = config.base_url,
            headers  = {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type":  "application/json",
            },
            timeout = config.timeout,
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def complete(self, system: str, user: str) -> str:
        """
        Single completion. Returns raw text response.
        Raises on HTTP error or timeout.
        """
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system",  "content": system},
                {"role": "user",    "content": user},
            ],
            "temperature": self.config.temperature,
            "max_tokens":  self.config.max_tokens,
        }
        resp = self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def complete_json(self, system: str, user: str) -> Any:
        """
        Completion with JSON parsing.
        Strips markdown fences if present.
        Raises json.JSONDecodeError if output is not valid JSON.
        """
        raw = self.complete(system, user).strip()

        # Strip markdown fences
        if raw.startswith("```"):
            lines = raw.split("\n")
            # Remove first and last fence lines
            lines = [l for l in lines if not l.startswith("```")]
            raw = "\n".join(lines).strip()

        return json.loads(raw)

    def health_check(self) -> bool:
        """Returns True if the endpoint is reachable."""
        try:
            resp = self._client.get("/models", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False


# ── Builder ───────────────────────────────────────────────────────────────────

class Builder:
    """
    Single Builder instance (Alpha or Beta).

    Receives source material, calls LLM, returns ClaimNodes.
    Stateless between calls — isolation is guaranteed by design.

    Usage:
        config  = BuilderConfig.alpha()
        builder = Builder(config)

        work    = WorkSource(title="...", content="...", ...)
        claims  = builder.process_work(work)

        concept = ConceptSource(name="...", definition="...", ...)
        claims  = builder.process_concept(concept)
    """

    def __init__(self, config: BuilderConfig):
        self.config = config
        self._parser = ClaimParser()
        log.info(
            f"Builder {config.origin.value} initialized: "
            f"model={config.model} url={config.base_url}"
        )

    def health_check(self) -> bool:
        with LLMClient(self.config) as client:
            ok = client.health_check()
        if not ok:
            log.warning(
                f"Builder {self.config.origin.value}: backend not reachable "
                f"at {self.config.base_url}"
            )
        return ok

    def process_work(self, work: "WorkSource") -> list[ClaimNode]:
        """
        Extract claims from a scientific work.
        Returns list of ClaimNodes tagged with this builder's origin.
        """
        prompt = USER_PROMPT_WORK.format(
            title      = work.title,
            authors    = ", ".join(work.authors),
            year       = work.year or "unknown",
            venue      = work.venue or "unknown",
            doi        = work.doi or "unknown",
            openalex_id = work.openalex_id or "unknown",
            content    = work.content[:4000],  # trim to avoid context overflow
        )

        source_ref = work.openalex_id or work.doi or work.title[:40]
        return self._call_and_parse(prompt, source_ref)

    def process_concept(self, concept: "ConceptSource") -> list[ClaimNode]:
        """
        Extract claims from an ontological concept (OpenCyc).
        """
        prompt = USER_PROMPT_CONCEPT.format(
            name        = concept.name,
            definition  = concept.definition or "(no definition provided)",
            broader     = ", ".join(concept.broader) or "none",
            narrower    = ", ".join(concept.narrower[:10]) or "none",
        )

        source_ref = f"cyc:{concept.cyc_id or concept.name}"
        return self._call_and_parse(prompt, source_ref)

    def _call_and_parse(self, user_prompt: str, source_ref: str) -> list[ClaimNode]:
        t0 = time.time()
        try:
            with LLMClient(self.config) as client:
                raw = client.complete_json(SYSTEM_PROMPT, user_prompt)
        except httpx.ConnectError as e:
            raise ConnectionError(
                f"Builder {self.config.origin.value}: cannot connect to "
                f"{self.config.base_url}. Is LM Studio running?"
            ) from e
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Builder {self.config.origin.value}: LLM returned non-JSON output. "
                f"Check model and prompt. Error: {e}"
            ) from e

        elapsed = time.time() - t0

        if not isinstance(raw, list):
            log.warning(
                f"Builder {self.config.origin.value}: LLM returned {type(raw).__name__} "
                f"instead of list — wrapping."
            )
            raw = [raw] if isinstance(raw, dict) else []

        claims = self._parser.parse(raw, source_ref, self.config.origin)
        log.info(
            f"Builder {self.config.origin.value}: {len(claims)} claims extracted "
            f"from {source_ref!r} in {elapsed:.1f}s"
        )
        return claims


# ── Source data structures ────────────────────────────────────────────────────

@dataclass
class WorkSource:
    """
    A scientific work to be processed by a Builder.
    Constructed from OpenAlex API response (see sources.py).
    """
    title:       str
    content:     str              # abstract or full text
    authors:     list[str] = field(default_factory=list)
    year:        Optional[int] = None
    venue:       str = ""
    doi:         str = ""
    openalex_id: str = ""

    @classmethod
    def from_openalex(cls, data: dict) -> "WorkSource":
        """Construct from OpenAlex API work object."""
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in data.get("authorships", [])
        ]
        abstract = data.get("abstract_inverted_index")
        if abstract:
            content = _reconstruct_abstract(abstract)
        else:
            content = data.get("title", "")

        return cls(
            title       = data.get("title", ""),
            content     = content,
            authors     = [a for a in authors if a],
            year        = data.get("publication_year"),
            venue       = data.get("primary_location", {}).get("source", {}).get("display_name", ""),
            doi         = data.get("doi", ""),
            openalex_id = data.get("id", ""),
        )


@dataclass
class ConceptSource:
    """
    An ontological concept to be processed by a Builder.
    Constructed from OpenCyc or similar ontology.
    """
    name:       str
    definition: str = ""
    broader:    list[str] = field(default_factory=list)
    narrower:   list[str] = field(default_factory=list)
    cyc_id:     str = ""


# ── Ontology Mapping Confidence Layer (v2.2 Sprint 3) ────────────────────────

class MappingConfidence(str, Enum):
    """
    Confidence level of an ontology concept mapping.

    [HEURISTIC] Thresholds are reference defaults — calibrate against gold labels
    in production.

    Anti-pattern: treating uncertain mappings as ground truth silently.
    Better explicit UNMAPPED than falsely confident MAPPED.
    """
    MAPPED              = "mapped"
    CANDIDATE           = "candidate"
    LOW_CONFIDENCE      = "low_confidence"
    MULTIPLE_CANDIDATES = "multiple_candidates"
    UNMAPPED            = "unmapped"
    EXCLUDED            = "excluded"


@dataclass
class ConceptMappingResult:
    """
    Result of mapping a text term to an ontology concept.

    Never silently assumes certainty — always carries a MappingConfidence.
    Used by OpenCycLoader.map_term() and Builder concept resolution.

    Fields
    ------
    input_term         Original term being mapped
    matched_concept    Best ConceptSource match (None if UNMAPPED/EXCLUDED)
    confidence         MappingConfidence level
    confidence_score   Raw score [0.0–1.0]
    candidates         All candidates (relevant for MULTIPLE_CANDIDATES)
    mapping_notes      Human-readable explanation
    ontology_source    Which ontology was queried
    """
    input_term:       str
    matched_concept:  Optional[ConceptSource]
    confidence:       MappingConfidence
    confidence_score: float
    candidates:       list[ConceptSource] = field(default_factory=list)
    mapping_notes:    str = ""
    ontology_source:  str = "opencyc"

    @property
    def is_usable(self) -> bool:
        """True if reliable enough for graph inclusion without manual review."""
        return self.confidence in {MappingConfidence.MAPPED, MappingConfidence.CANDIDATE}

    @property
    def requires_review(self) -> bool:
        return self.confidence in {
            MappingConfidence.LOW_CONFIDENCE,
            MappingConfidence.MULTIPLE_CANDIDATES,
        }

    def to_dict(self) -> dict:
        return {
            "input_term":       self.input_term,
            "matched_concept":  self.matched_concept.name if self.matched_concept else None,
            "confidence":       self.confidence.value,
            "confidence_score": self.confidence_score,
            "candidates":       [c.name for c in self.candidates],
            "mapping_notes":    self.mapping_notes,
            "ontology_source":  self.ontology_source,
            "is_usable":        self.is_usable,
            "requires_review":  self.requires_review,
        }


def _reconstruct_abstract(inverted_index: dict) -> str:
    """
    OpenAlex stores abstracts as inverted index {word: [positions]}.
    Reconstruct to readable string.
    """
    if not inverted_index:
        return ""
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in word_positions)


# ── Dual-Builder orchestrator ─────────────────────────────────────────────────

class DualBuilderPipeline:
    """
    Runs Alpha and Beta in sequence (or parallel) on the same source.
    Returns (claims_alpha, claims_beta) — completely separate, no mixing.

    The DBA isolation guarantee (Section V-A.5) is enforced by:
    - Separate Builder instances
    - Separate LLMClient connections
    - No shared state between process_* calls

    Usage:
        pipeline = DualBuilderPipeline()
        alpha_claims, beta_claims = pipeline.process_work(work_source)
    """

    def __init__(
        self,
        config_alpha: BuilderConfig | None = None,
        config_beta:  BuilderConfig | None = None,
    ):
        self.alpha = Builder(config_alpha or BuilderConfig.alpha())
        self.beta  = Builder(config_beta  or BuilderConfig.beta())

    def health_check(self) -> dict[str, bool]:
        return {
            "alpha": self.alpha.health_check(),
            "beta":  self.beta.health_check(),
        }

    def process_work(
        self, work: WorkSource
    ) -> tuple[list[ClaimNode], list[ClaimNode]]:
        """
        Process the same WorkSource through both Builders independently.
        Returns (alpha_claims, beta_claims).
        """
        log.info(f"DBA: processing work {work.openalex_id or work.title[:40]!r}")
        claims_alpha = self.alpha.process_work(work)
        claims_beta  = self.beta.process_work(work)
        log.info(
            f"DBA: alpha={len(claims_alpha)} claims, beta={len(claims_beta)} claims"
        )
        return claims_alpha, claims_beta

    def process_concept(
        self, concept: ConceptSource
    ) -> tuple[list[ClaimNode], list[ClaimNode]]:
        """
        Process the same ConceptSource through both Builders independently.
        """
        log.info(f"DBA: processing concept {concept.name!r}")
        claims_alpha = self.alpha.process_concept(concept)
        claims_beta  = self.beta.process_concept(concept)
        log.info(
            f"DBA: alpha={len(claims_alpha)} claims, beta={len(claims_beta)} claims"
        )
        return claims_alpha, claims_beta
