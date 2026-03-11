#!/usr/bin/env python3
"""
openalex_ingest.py — OpenAlex → Alexandria Claims

Fetches scientific papers from OpenAlex and creates protocol-compliant
Alexandria claims — either rule-based (no LLM) or via an LLM backend.

Extraction modes:
  rule-based (default)
    1. [Author] CONTRIBUTES_TO [Paper]   — EMPIRICAL/suggestion
    2. [Paper]  RELATES_TO [Concept]     — MODEL/suggestion (OpenAlex ML tags)
    3. [Paper]  MENTIONS [Abstract]      — SPECULATIVE/hypothesis

  LLM mode  (--llm-key KEY)
    Uses the existing Builder from builder.py against any OpenAI-compatible
    endpoint (default: DeepSeek).  Produces richer, semantically grounded
    claims extracted from the full abstract.

Usage:
    python openalex_ingest.py "climate change" --max 10
    python openalex_ingest.py "CRISPR" --max 5 --email you@example.com
    python openalex_ingest.py "mRNA vaccines" --llm-key $DEEPSEEK_API_KEY
    python openalex_ingest.py "any topic" --demo
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

# ── Alexandria core imports ────────────────────────────────────────────────────
# The package modules use relative imports (from .schema import …).
# We register the package directory under a clean name so they resolve correctly.
import importlib.util
import types

_PKG_PATH = Path(__file__).resolve().parent
_PKG_NAME = "alexandria_core"

_pkg_stub = types.ModuleType(_PKG_NAME)
_pkg_stub.__path__    = [str(_PKG_PATH)]
_pkg_stub.__package__ = _PKG_NAME
sys.modules[_PKG_NAME] = _pkg_stub

def _load_submodule(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{_PKG_NAME}.{name}",
        str(_PKG_PATH / f"{name}.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG_NAME
    sys.modules[f"{_PKG_NAME}.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod

_load_submodule("schema")
_load_submodule("patch")

from alexandria_core.schema import (   # noqa: E402
    ClaimNode, Category, Modality, EpistemicStatus, BuilderOrigin,
)
from alexandria_core.patch import PatchChain, PatchEmitter  # noqa: E402

log = logging.getLogger(__name__)

OPENALEX_BASE    = "https://api.openalex.org"
DEEPSEEK_API_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL   = "deepseek-chat"
RATE_LIMIT_S     = 0.12   # seconds between requests (polite pool)


# ── OpenAlex helpers ───────────────────────────────────────────────────────────

def fetch_works(
    query:       str,
    max_results: int  = 10,
    email:       str  = "",
    from_year:   int | None = None,
) -> list[dict]:
    """
    Fetch works from OpenAlex including concept tags.
    Uses cursor pagination to handle large result sets.
    """
    headers = {"User-Agent": f"AlexandriaIngest/0.1 ({email or 'anonymous'})"}
    params: dict = {
        "search":   query,
        "per-page": min(max_results, 50),
        "select":   (
            "id,title,doi,publication_year,authorships,"
            "primary_location,abstract_inverted_index,concepts"
        ),
    }
    if email:
        params["mailto"] = email
    if from_year:
        params["filter"] = f"publication_year:>={from_year}"

    works:      list[dict] = []
    cursor:     str        = "*"
    last_req:   float      = 0.0

    with httpx.Client(base_url=OPENALEX_BASE, headers=headers, timeout=30.0) as client:
        while len(works) < max_results:
            params["cursor"] = cursor

            elapsed = time.time() - last_req
            if elapsed < RATE_LIMIT_S:
                time.sleep(RATE_LIMIT_S - elapsed)
            last_req = time.time()

            try:
                resp = client.get("/works", params=params)
                resp.raise_for_status()
            except (httpx.ProxyError, httpx.ConnectError, httpx.HTTPStatusError) as e:
                raise httpx.ConnectError(str(e)) from e

            data   = resp.json()
            batch  = data.get("results", [])
            if not batch:
                break
            works.extend(batch)

            cursor = (data.get("meta") or {}).get("next_cursor")
            if not cursor:
                break

    return works[:max_results]


def demo_works(query: str, n: int = 5) -> list[dict]:
    """
    Generate synthetic OpenAlex-shaped work dicts for offline testing.
    Covers all three claim types (authorship, concept, abstract).
    """
    topics = query.split()[:3] or ["science"]
    base_abstract = (
        f"This study investigates {query} using a systematic approach. "
        "Results indicate a significant relationship between the variables. "
        "The findings contribute to the existing literature on this topic."
    )
    def _invert(text: str) -> dict:
        idx: dict[str, list[int]] = {}
        for i, w in enumerate(text.split()):
            idx.setdefault(w, []).append(i)
        return idx

    concepts_pool = [
        {"display_name": "Machine Learning",            "score": 0.85, "field": {"display_name": "Computer Science"}},
        {"display_name": "Data Science",                "score": 0.72, "field": {"display_name": "Computer Science"}},
        {"display_name": "Public Health",               "score": 0.65, "field": {"display_name": "Medicine"}},
        {"display_name": "Climate Change",              "score": 0.60, "field": {"display_name": "Earth Sciences"}},
        {"display_name": "Epidemiology",                "score": 0.55, "field": {"display_name": "Medicine"}},
        {"display_name": "Genomics",                    "score": 0.50, "field": {"display_name": "Biology"}},
        {"display_name": "Natural Language Processing", "score": 0.48, "field": {"display_name": "Computer Science"}},
    ]
    works = []
    for i in range(n):
        year = 2018 + (i % 6)
        title = f"Study on {query}: A systematic analysis (Part {i + 1})"
        works.append({
            "id":                    f"https://openalex.org/W{10000000 + i}",
            "title":                 title,
            "doi":                   f"https://doi.org/10.1234/demo.{i:04d}",
            "publication_year":      year,
            "authorships": [
                {"author": {"display_name": f"Author A{i}"}},
                {"author": {"display_name": f"Author B{i}"}},
            ],
            "primary_location": {
                "source": {"display_name": f"Journal of {topics[0].capitalize()} Research"}
            },
            "abstract_inverted_index": _invert(base_abstract),
            "concepts": concepts_pool[i % len(concepts_pool): i % len(concepts_pool) + 3],
        })
    return works


def reconstruct_abstract(inverted_index: dict) -> str:
    """Reconstruct readable abstract from OpenAlex inverted index format."""
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, locs in inverted_index.items():
        for pos in locs:
            positions[pos] = word
    return " ".join(positions[k] for k in sorted(positions))


# ── Rule-based claim extractor ─────────────────────────────────────────────────

_BASE_ASSUMPTIONS = ["SourceScope_OpenAlex", "AutoExtracted_RuleBasedIngest"]


def work_to_claims(work: dict) -> list[ClaimNode]:
    """
    Derive protocol-compliant ClaimNodes from an OpenAlex work dict
    using rule-based heuristics (no LLM).
    """
    claims: list[ClaimNode] = []

    title       = (work.get("title") or "Untitled").strip()
    short_title = title[:100]
    openalex_id = work.get("id", "")
    year        = work.get("publication_year")
    doi         = work.get("doi", "")
    source_ref  = openalex_id or doi or title[:40]
    work_id_tag = openalex_id.rsplit("/", 1)[-1] if openalex_id else "unknown"

    time_scope: dict = {}
    if year:
        time_scope = {"start_year": year, "end_year": year}

    venue = (
        (work.get("primary_location") or {})
        .get("source", {})
        .get("display_name", "")
    )

    # ── 1. Author CONTRIBUTES_TO Work ─────────────────────────────────────────
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in (work.get("authorships") or [])
    ]
    authors = [a for a in authors if a]

    for author in authors[:4]:
        # modality=SUGGESTION: rule-based extraction has no sigma value.
        claim = ClaimNode.new(
            subject   = author,
            predicate = "CONTRIBUTES_TO",
            object    = short_title,
            category  = Category.EMPIRICAL,
            modality  = Modality.SUGGESTION,
            assumptions = [
                *_BASE_ASSUMPTIONS,
                "AuthorshipRecord_OpenAlexRegistry",
                f"WorkID_{work_id_tag}",
            ],
            source_refs    = [source_ref],
            scope          = {"domain": "academic_authorship"},
            time_scope     = time_scope,
            builder_origin = BuilderOrigin.ALPHA,
        )
        claims.append(claim)

    # ── 2. Work RELATES_TO Concept (OpenAlex ML tagging) ─────────────────────
    concepts = work.get("concepts") or []
    for concept in concepts[:5]:
        name  = (concept.get("display_name") or "").strip()
        score = float(concept.get("score") or 0.0)
        if not name or score < 0.3:
            continue

        field_name = (
            (concept.get("field") or {}).get("display_name", "")
            or (concept.get("domain") or {}).get("display_name", "")
            or "academic"
        )

        claim = ClaimNode.new(
            subject   = short_title,
            predicate = "RELATES_TO",
            object    = name,
            category  = Category.MODEL,
            modality  = Modality.SUGGESTION,
            assumptions = [
                *_BASE_ASSUMPTIONS,
                "OpenAlexConceptModel_ML",
                f"ConceptScore_{score:.2f}",
            ],
            source_refs = [source_ref],
            qualifiers  = {"concept_score": round(score, 4), "openalex_concept": True},
            scope       = {"domain": field_name},
            time_scope  = time_scope,
            builder_origin = BuilderOrigin.ALPHA,
        )
        claims.append(claim)

    # ── 3. Work MENTIONS first sentence of abstract ───────────────────────────
    abstract_raw = work.get("abstract_inverted_index")
    if abstract_raw:
        abstract   = reconstruct_abstract(abstract_raw)
        first_sent = abstract.split(".")[0].strip()
        if len(first_sent) > 30:
            claim = ClaimNode.new(
                subject   = short_title,
                predicate = "MENTIONS",
                object    = first_sent[:250],
                category  = Category.SPECULATIVE,
                modality  = Modality.HYPOTHESIS,
                assumptions = [
                    *_BASE_ASSUMPTIONS,
                    "AbstractFirstSentenceHeuristic",
                    "NotLLMValidated",
                ],
                source_refs    = [source_ref],
                scope          = {"domain": venue or "academic"},
                time_scope     = time_scope,
                builder_origin = BuilderOrigin.ALPHA,
            )
            claims.append(claim)

    return claims


# ── LLM-based claim extractor ──────────────────────────────────────────────────

def llm_extract_claims(
    works:    list[dict],
    api_url:  str,
    api_key:  str,
    model:    str,
    verbose:  bool = False,
) -> tuple[list[ClaimNode], list[str]]:
    """
    Use an OpenAI-compatible LLM to extract structured claims from each work.

    Delegates to the existing Builder from builder.py.
    Works with DeepSeek, OpenAI, Ollama (OpenAI-compat mode), or LM Studio.

    Returns (claims, errors).
    """
    # Lazy-load builder (imports httpx which must be installed)
    if f"{_PKG_NAME}.builder" not in sys.modules:
        _load_submodule("builder")

    from alexandria_core.builder import Builder, BuilderConfig, WorkSource

    config = BuilderConfig(
        origin      = BuilderOrigin.ALPHA,
        base_url    = api_url,
        api_key     = api_key,
        model       = model,
        temperature = 0.2,
        max_tokens  = 2048,
        timeout     = 120.0,
    )
    builder = Builder(config)

    all_claims: list[ClaimNode] = []
    errors:     list[str]       = []

    for work in works:
        title = (work.get("title") or "Untitled")[:60]
        work_source = WorkSource.from_openalex(work)
        try:
            claims = builder.process_work(work_source)
            all_claims.extend(claims)
            if verbose:
                log.debug(f"LLM extracted {len(claims)} claims from {title!r}")
        except ConnectionError as e:
            msg = f"LLM connection error for {title!r}: {e}"
            errors.append(msg)
            log.error(msg)
        except ValueError as e:
            msg = f"LLM parse error for {title!r}: {e}"
            errors.append(msg)
            log.warning(msg)
        except httpx.ProxyError as e:
            msg = f"Network proxy blocked request for {title!r}: {e}"
            errors.append(msg)
            log.error(msg)
        except Exception as e:
            msg = f"Unexpected error for {title!r}: {type(e).__name__}: {e}"
            errors.append(msg)
            log.error(msg)

    return all_claims, errors


# ── Ingest pipeline ────────────────────────────────────────────────────────────

def ingest(
    query:       str,
    max_results: int,
    email:       str,
    from_year:   int | None,
    output:      str | None,
    verbose:     bool,
    demo:        bool = False,
    llm_key:     str  = "",
    llm_url:     str  = DEEPSEEK_API_URL,
    llm_model:   str  = DEEPSEEK_MODEL,
) -> int:
    logging.basicConfig(
        level   = logging.DEBUG if verbose else logging.WARNING,
        format  = "%(levelname)s %(name)s %(message)s",
    )

    use_llm = bool(llm_key)
    mode    = "LLM" if use_llm else ("DEMO" if demo else "rule-based")

    print(f"\n[Alexandria Ingest]  query={query!r}  max={max_results}  mode={mode}")
    if from_year:
        print(f"                     from_year={from_year}")
    if use_llm:
        print(f"                     llm_url={llm_url}  model={llm_model}")

    # ── Step 1: Fetch ──────────────────────────────────────────────────────────
    if demo:
        print("\n[1/3] Generating synthetic demo works …")
        works = demo_works(query, max_results)
        print(f"      {len(works)} synthetic works generated.")
    else:
        print("\n[1/3] Fetching from OpenAlex …")
        try:
            works = fetch_works(query, max_results, email, from_year)
        except httpx.HTTPStatusError as e:
            print(f"      ERROR: OpenAlex returned HTTP {e.response.status_code}", file=sys.stderr)
            return 1
        except httpx.ConnectError:
            print("      ERROR: Cannot connect to api.openalex.org — check network", file=sys.stderr)
            print("      TIP: Use --demo for offline testing with synthetic data", file=sys.stderr)
            return 1
        print(f"      {len(works)} works retrieved.")

    # ── Step 2: Extract claims ─────────────────────────────────────────────────
    print(f"\n[2/3] Extracting claims ({mode}) …")
    llm_errors: list[str] = []

    if use_llm:
        raw_claims, llm_errors = llm_extract_claims(
            works, llm_url, llm_key, llm_model, verbose
        )
        if llm_errors:
            for e in llm_errors:
                print(f"      WARN: {e}", file=sys.stderr)
    else:
        raw_claims = []
        for work in works:
            raw_claims.extend(work_to_claims(work))

    # ── Step 3: Feed into patch chain ─────────────────────────────────────────
    print("\n[3/3] Building patch chain …")
    chain:       PatchChain   = PatchChain()
    emitter:     PatchEmitter = PatchEmitter(chain)
    all_claims:  list[ClaimNode] = []
    skip_errors: list[str]       = []

    for claim in raw_claims:
        title_hint = claim.subject[:50]
        try:
            time.sleep(0.001)   # ensure strictly monotonic float timestamps
            emitter.add(claim)
            all_claims.append(claim)
        except ValueError as e:
            msg = f"{claim.claim_id[:8]}… ({claim.predicate}): {e}"
            skip_errors.append(msg)
            log.warning(f"Skipped: {msg}")

    print(f"      Claims accepted: {len(all_claims)}")
    print(f"      Claims skipped:  {len(skip_errors)}")

    ok, violations = chain.verify_integrity()
    integrity_str = "OK" if ok else f"FAILED ({len(violations)} violation(s))"
    print(f"      Chain: {chain.length} patches  |  integrity = {integrity_str}")
    if violations:
        for v in violations:
            print(f"      !! {v}", file=sys.stderr)

    # ── Build report ──────────────────────────────────────────────────────────
    category_counts:  dict[str, int] = {}
    predicate_counts: dict[str, int] = {}
    for c in all_claims:
        category_counts[c.category.value]  = category_counts.get(c.category.value, 0)  + 1
        predicate_counts[c.predicate]       = predicate_counts.get(c.predicate, 0)       + 1

    report = {
        "query":            query,
        "mode":             mode,
        "from_year":        from_year,
        "works_fetched":    len(works),
        "claims_total":     len(all_claims),
        "claims_skipped":   len(skip_errors),
        "llm_errors":       len(llm_errors),
        "chain_length":     chain.length,
        "chain_head":       chain.head_hash[:20] + "…" if chain.head_hash != "0" * 64 else "(empty)",
        "chain_integrity":  "ok" if ok else "FAILED",
        "by_category":      category_counts,
        "by_predicate":     predicate_counts,
        "errors":           skip_errors + llm_errors,
        "claims": [
            {
                "id":        c.claim_id[:8] + "…",
                "subject":   c.subject[:80],
                "predicate": c.predicate,
                "object":    c.object[:80],
                "category":  c.category.value,
                "modality":  c.modality.value,
                "source":    (c.source_refs[0] if c.source_refs else ""),
            }
            for c in all_claims
        ],
    }

    report_json = json.dumps(report, indent=2, ensure_ascii=False)

    if output:
        Path(output).write_text(report_json, encoding="utf-8")
        print(f"\nReport saved → {output}")
    else:
        print("\n" + report_json)

    return 0 if ok else 1


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch OpenAlex papers and build Alexandria claims.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Extraction modes:
  default       Rule-based (no LLM, deterministic)
  --llm-key KEY LLM-based via OpenAI-compatible endpoint (DeepSeek by default)
  --demo        Offline test with synthetic data (no network)

Examples:
  python openalex_ingest.py "climate change" --max 10
  python openalex_ingest.py "CRISPR" --max 5 --email you@example.com
  python openalex_ingest.py "mRNA vaccines" --llm-key $DEEPSEEK_API_KEY --max 3
  python openalex_ingest.py "mRNA vaccines" --llm-key $DEEPSEEK_API_KEY \\
      --llm-url https://api.deepseek.com/v1 --llm-model deepseek-chat
  python openalex_ingest.py "any topic" --demo
""",
    )
    parser.add_argument("query",       help="OpenAlex full-text search query")
    parser.add_argument("--max",       type=int,  default=10, dest="max_results",
                        help="Max papers to fetch (default: 10)")
    parser.add_argument("--email",     default="",
                        help="Email for OpenAlex polite pool (faster rate limits)")
    parser.add_argument("--from-year", type=int, default=None, dest="from_year",
                        help="Filter: only papers from this year onwards")
    parser.add_argument("--output",    default=None,
                        help="Save JSON report to file (default: print to stdout)")
    parser.add_argument("--verbose",   action="store_true",
                        help="Enable debug logging")
    parser.add_argument("--demo",      action="store_true",
                        help="Use synthetic data instead of live OpenAlex API (offline test)")

    # LLM options
    llm = parser.add_argument_group("LLM extraction (optional)")
    llm.add_argument("--llm-key",   default=os.environ.get("DEEPSEEK_API_KEY", ""),
                     help="API key for LLM backend (or set DEEPSEEK_API_KEY env var)")
    llm.add_argument("--llm-url",   default=DEEPSEEK_API_URL,
                     help=f"LLM base URL (default: {DEEPSEEK_API_URL})")
    llm.add_argument("--llm-model", default=DEEPSEEK_MODEL,
                     help=f"Model name (default: {DEEPSEEK_MODEL})")

    args = parser.parse_args()
    sys.exit(ingest(
        query       = args.query,
        max_results = args.max_results,
        email       = args.email,
        from_year   = args.from_year,
        output      = args.output,
        verbose     = args.verbose,
        demo        = args.demo,
        llm_key     = args.llm_key,
        llm_url     = args.llm_url,
        llm_model   = args.llm_model,
    ))


if __name__ == "__main__":
    main()
