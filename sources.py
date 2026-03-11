"""
Alexandria Core — sources.py
Source ingestion: OpenAlex (scientific works) and OpenCyc (ontology).

Phase 1 of the DBA pipeline (Section V-A.4):
Pre-epistemic data preparation. Sources are not evaluated here —
that is the Builder's responsibility.

All source references are preserved as source_refs[] on every
derived node (Seal Criterion D.2: Source Traceability).

Requirements:
    pip install httpx

Environment variables:
    ALEXANDRIA_OPENALEX_EMAIL   recommended for polite pool (faster rate limits)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import httpx

from .builder import WorkSource, ConceptSource
from .schema import WorkNode, AuthorNode, InstitutionNode, ConceptNode

log = logging.getLogger(__name__)


# ── OpenAlex client ───────────────────────────────────────────────────────────

class OpenAlexClient:
    """
    Client for the OpenAlex REST API.
    https://docs.openalex.org

    Implements polite pool (mailto parameter) and rate limiting.
    All responses are cached locally to avoid redundant API calls.

    Usage:
        client = OpenAlexClient()
        for work in client.search_works("remote work productivity", max_results=50):
            print(work.title)
    """

    BASE_URL    = "https://api.openalex.org"
    RATE_LIMIT  = 0.12   # seconds between requests (< 10 req/s polite pool)

    def __init__(
        self,
        email:      str | None = None,
        cache_dir:  str | None = None,
    ):
        self.email     = email or os.environ.get("ALEXANDRIA_OPENALEX_EMAIL", "")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._client = httpx.Client(
            base_url = self.BASE_URL,
            headers  = {"User-Agent": f"AlexandriaProtocol/1.0 ({self.email})"},
            timeout  = 30.0,
        )
        self._last_request = 0.0

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── Rate limiting ─────────────────────────────────────────────────────────

    def _wait(self):
        elapsed = time.time() - self._last_request
        if elapsed < self.RATE_LIMIT:
            time.sleep(self.RATE_LIMIT - elapsed)
        self._last_request = time.time()

    # ── Cache ─────────────────────────────────────────────────────────────────

    def _cache_path(self, key: str) -> Optional[Path]:
        if not self.cache_dir:
            return None
        safe = key.replace("/", "_").replace(":", "_")[:120]
        return self.cache_dir / f"{safe}.json"

    def _cache_get(self, key: str) -> Any | None:
        path = self._cache_path(key)
        if path and path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                return None
        return None

    def _cache_set(self, key: str, data: Any):
        path = self._cache_path(key)
        if path:
            path.write_text(json.dumps(data, ensure_ascii=False))

    # ── API calls ─────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict:
        cache_key = path + json.dumps(params or {}, sort_keys=True)
        cached = self._cache_get(cache_key)
        if cached is not None:
            log.debug(f"Cache hit: {path}")
            return cached

        self._wait()
        params = params or {}
        if self.email:
            params["mailto"] = self.email

        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        data = resp.json()
        self._cache_set(cache_key, data)
        return data

    def get_work(self, openalex_id: str) -> dict:
        """Fetch a single work by OpenAlex ID (W...) or DOI."""
        if openalex_id.startswith("https://doi.org/"):
            path = f"/works/{openalex_id}"
        elif openalex_id.startswith("W"):
            path = f"/works/{openalex_id}"
        else:
            path = f"/works/https://doi.org/{openalex_id}"
        return self._get(path)

    def search_works(
        self,
        query:       str,
        max_results: int   = 25,
        from_year:   int | None = None,
        to_year:     int | None = None,
        open_access: bool  = False,
    ) -> Iterator[WorkSource]:
        """
        Search OpenAlex for works matching a query.
        Yields WorkSource objects ready for Builder ingestion.
        """
        params: dict = {
            "search":    query,
            "per-page":  min(max_results, 200),
            "select":    "id,title,doi,publication_year,authorships,"
                         "primary_location,abstract_inverted_index",
        }
        if from_year:
            params["filter"] = f"publication_year:>{from_year - 1}"
        if to_year:
            existing = params.get("filter", "")
            year_filter = f"publication_year:<{to_year + 1}"
            params["filter"] = f"{existing},{year_filter}" if existing else year_filter
        if open_access:
            oa_filter = "open_access.is_oa:true"
            existing = params.get("filter", "")
            params["filter"] = f"{existing},{oa_filter}" if existing else oa_filter

        fetched = 0
        cursor  = "*"

        while fetched < max_results:
            params["cursor"] = cursor
            data = self._get("/works", params)
            results = data.get("results", [])
            if not results:
                break

            for item in results:
                if fetched >= max_results:
                    return
                yield WorkSource.from_openalex(item)
                fetched += 1

            # Pagination
            meta   = data.get("meta", {})
            cursor = meta.get("next_cursor")
            if not cursor:
                break

    def get_work_source(self, openalex_id: str) -> WorkSource:
        """Fetch a single work as WorkSource."""
        data = self.get_work(openalex_id)
        return WorkSource.from_openalex(data)

    def build_work_node(self, data: dict) -> WorkNode:
        """Convert OpenAlex work dict to a WorkNode for the graph."""
        return WorkNode.new(
            title       = data.get("title", ""),
            doi         = data.get("doi", ""),
            year        = data.get("publication_year"),
            venue       = data.get("primary_location", {})
                              .get("source", {}).get("display_name", ""),
            openalex_id = data.get("id", ""),
            source_refs = [data.get("id", "")],
        )

    def build_author_nodes(self, data: dict) -> list[AuthorNode]:
        """Extract AuthorNodes from a work dict."""
        authors = []
        for authorship in data.get("authorships", []):
            a = authorship.get("author", {})
            if not a.get("display_name"):
                continue
            inst_id = ""
            insts   = authorship.get("institutions", [])
            if insts:
                inst_id = insts[0].get("id", "")
            authors.append(AuthorNode.new(
                name        = a["display_name"],
                orcid       = a.get("orcid", ""),
                openalex_id = a.get("id", ""),
                institution_id = inst_id,
            ))
        return authors


# ── OpenCyc loader ────────────────────────────────────────────────────────────

class OpenCycLoader:
    """
    Loads concepts from a local OpenCyc export or compatible ontology file.

    OpenCyc is available as:
    - RDF/OWL dump: https://github.com/asanchez75/opencyc
    - JSON: custom export

    This loader handles a simple JSON format:
    [
      {
        "id":         "Mx4rvVi...",
        "name":       "Dog",
        "definition": "A domesticated canid...",
        "broader":    ["Animal", "Pet"],
        "narrower":   ["Labrador", "Poodle"]
      },
      ...
    ]

    For a minimal bootstrap, a small built-in concept set is also provided.
    """

    def __init__(self, json_path: str | Path | None = None):
        self._path     = Path(json_path) if json_path else None
        self._concepts: list[dict] = []
        self._loaded   = False

    def load(self) -> "OpenCycLoader":
        """Load concepts from file. Falls back to built-in set if no file given."""
        if self._path and self._path.exists():
            self._concepts = json.loads(self._path.read_text())
            log.info(f"Loaded {len(self._concepts)} OpenCyc concepts from {self._path}")
        else:
            self._concepts = BUILTIN_CONCEPTS
            log.info(
                f"No OpenCyc file provided — using {len(self._concepts)} built-in concepts."
            )
        self._loaded = True
        return self

    def iter_concepts(self) -> Iterator[ConceptSource]:
        """Yield all loaded concepts as ConceptSource objects."""
        if not self._loaded:
            self.load()
        for c in self._concepts:
            yield ConceptSource(
                name       = c.get("name", ""),
                definition = c.get("definition", ""),
                broader    = c.get("broader", []),
                narrower   = c.get("narrower", []),
                cyc_id     = c.get("id", ""),
            )

    def get_concept(self, name: str) -> Optional[ConceptSource]:
        """Get a concept by name (case-insensitive)."""
        if not self._loaded:
            self.load()
        name_lower = name.lower()
        for c in self._concepts:
            if c.get("name", "").lower() == name_lower:
                return ConceptSource(
                    name       = c["name"],
                    definition = c.get("definition", ""),
                    broader    = c.get("broader", []),
                    narrower   = c.get("narrower", []),
                    cyc_id     = c.get("id", ""),
                )
        return None

    def build_concept_nodes(self) -> list[ConceptNode]:
        """Convert all loaded concepts to ConceptNode graph objects."""
        if not self._loaded:
            self.load()
        nodes = []
        for c in self._concepts:
            nodes.append(ConceptNode.new(
                name        = c.get("name", ""),
                definition  = c.get("definition", ""),
                broader     = c.get("broader", []),
                narrower    = c.get("narrower", []),
                source_refs = [f"cyc:{c.get('id', c.get('name', ''))}"],
            ))
        return nodes


# ── Built-in concept set (bootstrap without OpenCyc file) ────────────────────

BUILTIN_CONCEPTS = [
    {
        "id":         "cyc:Organization",
        "name":       "Organization",
        "definition": "A social group of people with a collective goal, often formalized by law.",
        "broader":    ["Agent", "SocialGroup"],
        "narrower":   ["Company", "GovernmentOrganization", "University", "ResearchInstitution"],
    },
    {
        "id":         "cyc:ResearchInstitution",
        "name":       "ResearchInstitution",
        "definition": "An organization primarily engaged in scientific research.",
        "broader":    ["Organization"],
        "narrower":   ["University", "NationalLaboratory", "ThinkTank"],
    },
    {
        "id":         "cyc:ScientificClaim",
        "name":       "ScientificClaim",
        "definition": "A proposition asserted as true based on empirical evidence or formal reasoning.",
        "broader":    ["Proposition", "EpistemicObject"],
        "narrower":   ["EmpiricalClaim", "TheoreticalClaim", "ModelBasedClaim"],
    },
    {
        "id":         "cyc:EmpiricalClaim",
        "name":       "EmpiricalClaim",
        "definition": "A claim based on observable evidence, subject to falsification.",
        "broader":    ["ScientificClaim"],
        "narrower":   ["StatisticalClaim", "ExperimentalResult"],
    },
    {
        "id":         "cyc:CausalRelation",
        "name":       "CausalRelation",
        "definition": "A relation where one event or state is a cause of another.",
        "broader":    ["BinaryRelation"],
        "narrower":   ["NecessaryCause", "SufficientCause", "ContributingFactor"],
    },
    {
        "id":         "cyc:EpistemicUncertainty",
        "name":       "EpistemicUncertainty",
        "definition": "Uncertainty arising from incomplete knowledge, reducible by more information.",
        "broader":    ["Uncertainty"],
        "narrower":   ["ModelUncertainty", "ParameterUncertainty"],
    },
    {
        "id":         "cyc:KnowledgeGraph",
        "name":       "KnowledgeGraph",
        "definition": "A structured representation of knowledge as a graph of entities and relations.",
        "broader":    ["InformationStructure", "Database"],
        "narrower":   ["OntologyGraph", "EpistemicGraph"],
    },
]


# ── Ontology Mapping via OpenCycLoader (v2.2 Sprint 3) ───────────────────────

# Import mapping types (defined in builder.py to avoid circular import)
def _get_mapping_types():
    from .builder import MappingConfidence, ConceptMappingResult, ConceptSource as CS
    return MappingConfidence, ConceptMappingResult, CS


def map_term(
    loader: "OpenCycLoader",
    term: str,
    threshold_mapped: float = 0.85,
    threshold_candidate: float = 0.55,
    threshold_low: float = 0.30,
) -> "ConceptMappingResult":
    """
    Map a text term to an ontology concept using normalised string similarity.

    This is a [HEURISTIC] reference implementation using simple name/definition
    matching. Production systems should replace this with:
    - Embedding-based similarity (sentence-transformers or similar)
    - BM25 over concept definitions
    - LLM-assisted disambiguation for MULTIPLE_CANDIDATES cases

    Confidence thresholds [HEURISTIC]:
        >= threshold_mapped    → MAPPED
        >= threshold_candidate → CANDIDATE
        >= threshold_low       → LOW_CONFIDENCE
        < threshold_low        → UNMAPPED (if any candidate found)
                               → UNMAPPED (if no candidate at all)

    Returns
    -------
    ConceptMappingResult — always returned, never raises.
    Use result.is_usable to decide if graph inclusion is safe.
    """
    MappingConfidence, ConceptMappingResult, CS = _get_mapping_types()

    if not loader._loaded:
        loader.load()

    term_norm = term.lower().strip()
    scored: list[tuple[float, object]] = []

    for concept_dict in loader._concepts:
        name  = concept_dict.get("name", "")
        defn  = concept_dict.get("definition", "")
        score = _similarity(term_norm, name.lower(), defn.lower())
        if score > 0:
            cs = CS(
                name       = name,
                definition = defn,
                broader    = concept_dict.get("broader", []),
                narrower   = concept_dict.get("narrower", []),
                cyc_id     = concept_dict.get("id", ""),
            )
            scored.append((score, cs))

    scored.sort(key=lambda x: -x[0])

    if not scored:
        return ConceptMappingResult(
            input_term=term, matched_concept=None,
            confidence=MappingConfidence.UNMAPPED, confidence_score=0.0,
            mapping_notes="No candidates found in ontology.",
        )

    top_score, top_concept = scored[0]
    all_candidates = [cs for _, cs in scored[:5]]

    # Multiple ambiguous candidates at similar score
    if len(scored) >= 2:
        second_score = scored[1][0]
        ambiguity_gap = top_score - second_score
        if top_score >= threshold_candidate and ambiguity_gap < 0.15:
            return ConceptMappingResult(
                input_term=term, matched_concept=top_concept,
                confidence=MappingConfidence.MULTIPLE_CANDIDATES,
                confidence_score=top_score,
                candidates=all_candidates,
                mapping_notes=(
                    f"Top two scores within {ambiguity_gap:.2f} of each other "
                    f"({top_score:.2f} vs {second_score:.2f}). Manual review recommended."
                ),
            )

    if top_score >= threshold_mapped:
        return ConceptMappingResult(
            input_term=term, matched_concept=top_concept,
            confidence=MappingConfidence.MAPPED, confidence_score=top_score,
            candidates=all_candidates,
            mapping_notes=f"High-confidence match (score={top_score:.2f}).",
        )
    elif top_score >= threshold_candidate:
        return ConceptMappingResult(
            input_term=term, matched_concept=top_concept,
            confidence=MappingConfidence.CANDIDATE, confidence_score=top_score,
            candidates=all_candidates,
            mapping_notes=f"Plausible match — verify before graph inclusion (score={top_score:.2f}).",
        )
    elif top_score >= threshold_low:
        return ConceptMappingResult(
            input_term=term, matched_concept=top_concept,
            confidence=MappingConfidence.LOW_CONFIDENCE, confidence_score=top_score,
            candidates=all_candidates,
            mapping_notes=f"Weak match — do not include without manual review (score={top_score:.2f}).",
        )
    else:
        return ConceptMappingResult(
            input_term=term, matched_concept=None,
            confidence=MappingConfidence.UNMAPPED, confidence_score=top_score,
            candidates=all_candidates,
            mapping_notes=f"No sufficiently confident match found (best score={top_score:.2f}).",
        )


def _similarity(term: str, name: str, definition: str) -> float:
    """
    Simple normalised similarity: exact name → 1.0, partial name/def overlap → [0, 1).

    [HEURISTIC] Replace with embedding similarity in production.
    """
    if term == name:
        return 1.0
    if term in name or name in term:
        overlap = len(set(term) & set(name)) / max(len(set(term) | set(name)), 1)
        return 0.7 + 0.15 * overlap
    # Word overlap against name + definition
    term_words  = set(term.split())
    target_text = (name + " " + definition).lower()
    target_words = set(target_text.split())
    if not term_words:
        return 0.0
    intersection = term_words & target_words
    return len(intersection) / len(term_words) * 0.6


# Attach as method to OpenCycLoader
OpenCycLoader.map_term = map_term
