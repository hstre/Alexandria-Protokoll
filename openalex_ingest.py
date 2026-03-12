#!/usr/bin/env python3
"""
openalex_ingest.py — OpenAlex → Alexandria Claims

Fetches scientific papers from OpenAlex and creates protocol-compliant
Alexandria claims in one of three modes:

  rule-based (default)
    Deterministic extraction from paper metadata — no LLM required.

  single-LLM  (--llm-key KEY)
    One Builder (Alpha) extracts claims via an OpenAI-compatible endpoint.
    Default endpoint: DeepSeek (api.deepseek.com/v1).

  dual-LLM    (--llm-key KEY  --llm-key-b KEY2)
    Full DBA pipeline: Alpha + Beta extract independently, DiffEngine
    detects disagreements, Adjudicator resolves them, then PatchChain.
    Typical setup: Alpha=DeepSeek, Beta=OpenRouter (different model).

Usage:
    python openalex_ingest.py "climate change" --max 10
    python openalex_ingest.py "mRNA vaccines" \\
        --llm-key $DEEPSEEK_API_KEY
    python openalex_ingest.py "mRNA vaccines" \\
        --llm-key  $DEEPSEEK_API_KEY \\
        --llm-key-b $OPENROUTER_API_KEY \\
        --llm-model-b "meta-llama/llama-3.1-8b-instruct"
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
from alexandria_core.schema import (
    ClaimNode, Category, Modality, BuilderOrigin,
)
from alexandria_core.patch import PatchChain, PatchEmitter

log = logging.getLogger(__name__)

OPENALEX_BASE      = "https://api.openalex.org"
DEEPSEEK_API_URL   = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL     = "deepseek-chat"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL   = "mistralai/mistral-7b-instruct"
RATE_LIMIT_S       = 0.12


# ── OpenAlex helpers ───────────────────────────────────────────────────────────

def fetch_works(
    query:       str,
    max_results: int  = 10,
    email:       str  = "",
    from_year:   int | None = None,
) -> list[dict]:
    """Fetch works from OpenAlex (with concepts). Cursor-paginated."""
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

    works: list[dict] = []
    cursor  = "*"
    last_req = 0.0

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
    """Generate synthetic OpenAlex-shaped work dicts for offline testing."""
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
    """Derive ClaimNodes from OpenAlex metadata (no LLM)."""
    claims: list[ClaimNode] = []
    title       = (work.get("title") or "Untitled").strip()
    short_title = title[:100]
    openalex_id = work.get("id", "")
    year        = work.get("publication_year")
    doi         = work.get("doi", "")
    source_ref  = openalex_id or doi or title[:40]
    work_id_tag = openalex_id.rsplit("/", 1)[-1] if openalex_id else "unknown"
    time_scope: dict = {"start_year": year, "end_year": year} if year else {}
    venue = ((work.get("primary_location") or {}).get("source", {}).get("display_name", ""))

    # 1. Author → Work
    authors = [a.get("author", {}).get("display_name", "") for a in (work.get("authorships") or [])]
    for author in [a for a in authors if a][:4]:
        claims.append(ClaimNode.new(
            subject=author, predicate="CONTRIBUTES_TO", object=short_title,
            category=Category.EMPIRICAL, modality=Modality.SUGGESTION,
            assumptions=[*_BASE_ASSUMPTIONS, "AuthorshipRecord_OpenAlexRegistry", f"WorkID_{work_id_tag}"],
            source_refs=[source_ref], scope={"domain": "academic_authorship"},
            time_scope=time_scope, builder_origin=BuilderOrigin.ALPHA,
        ))

    # 2. Work → Concept
    for concept in (work.get("concepts") or [])[:5]:
        name  = (concept.get("display_name") or "").strip()
        score = float(concept.get("score") or 0.0)
        if not name or score < 0.3:
            continue
        field_name = (concept.get("field") or {}).get("display_name", "") or "academic"
        claims.append(ClaimNode.new(
            subject=short_title, predicate="RELATES_TO", object=name,
            category=Category.MODEL, modality=Modality.SUGGESTION,
            assumptions=[*_BASE_ASSUMPTIONS, "OpenAlexConceptModel_ML", f"ConceptScore_{score:.2f}"],
            source_refs=[source_ref], qualifiers={"concept_score": round(score, 4)},
            scope={"domain": field_name}, time_scope=time_scope, builder_origin=BuilderOrigin.ALPHA,
        ))

    # 3. Work → Abstract snippet
    abstract_raw = work.get("abstract_inverted_index")
    if abstract_raw:
        first_sent = reconstruct_abstract(abstract_raw).split(".")[0].strip()
        if len(first_sent) > 30:
            claims.append(ClaimNode.new(
                subject=short_title, predicate="MENTIONS", object=first_sent[:250],
                category=Category.SPECULATIVE, modality=Modality.HYPOTHESIS,
                assumptions=[*_BASE_ASSUMPTIONS, "AbstractFirstSentenceHeuristic", "NotLLMValidated"],
                source_refs=[source_ref], scope={"domain": venue or "academic"},
                time_scope=time_scope, builder_origin=BuilderOrigin.ALPHA,
            ))

    return claims


# ── LLM extraction helpers ─────────────────────────────────────────────────────

def _make_builder(api_url: str, api_key: str, model: str, origin: BuilderOrigin):
    """Create a Builder instance for the given config."""
    from alexandria_core.builder import Builder, BuilderConfig
    config = BuilderConfig(
        origin=origin, base_url=api_url, api_key=api_key,
        model=model, temperature=0.2, max_tokens=2048, timeout=120.0,
    )
    return Builder(config)


def single_llm_extract(
    works:   list[dict],
    api_url: str,
    api_key: str,
    model:   str,
) -> tuple[list[ClaimNode], list[str]]:
    """Extract claims with one Builder (Alpha)."""
    from alexandria_core.builder import WorkSource

    builder = _make_builder(api_url, api_key, model, BuilderOrigin.ALPHA)
    all_claims: list[ClaimNode] = []
    errors:     list[str]       = []

    for work in works:
        title = (work.get("title") or "Untitled")[:60]
        try:
            claims = builder.process_work(WorkSource.from_openalex(work))
            all_claims.extend(claims)
            print(f"      [{len(claims):3d} claims]  {title} …")
        except httpx.ProxyError as e:
            msg = f"Proxy blocked {title!r}: {e}"
            errors.append(msg); log.error(msg)
        except ConnectionError as e:
            msg = f"LLM connection error for {title!r}: {e}"
            errors.append(msg); log.error(msg)
        except (ValueError, Exception) as e:
            msg = f"{type(e).__name__} for {title!r}: {e}"
            errors.append(msg); log.warning(msg)

    return all_claims, errors


def dual_llm_extract(
    works:       list[dict],
    cfg_alpha:   tuple[str, str, str],   # (url, key, model)
    cfg_beta:    tuple[str, str, str],
) -> tuple[list[ClaimNode], list[str], dict]:
    """
    Full DBA dual-builder: Alpha + Beta → DiffEngine → Adjudicator.
    Returns (resolved_claims, errors, diff_summary).
    """
    from alexandria_core.builder    import WorkSource
    from alexandria_core.diff       import DiffEngine
    from alexandria_core.adjudication import Adjudicator

    builder_a = _make_builder(*cfg_alpha, BuilderOrigin.ALPHA)
    builder_b = _make_builder(*cfg_beta,  BuilderOrigin.BETA)
    diff_eng  = DiffEngine()

    all_resolved: list[ClaimNode] = []
    errors:       list[str]       = []
    total_diffs   = 0

    for work in works:
        title  = (work.get("title") or "Untitled")[:60]
        source = WorkSource.from_openalex(work)
        source_ref = work.get("id") or work.get("doi") or title

        # ── Alpha ──────────────────────────────────────────────────────────────
        try:
            claims_a = builder_a.process_work(source)
        except Exception as e:
            msg = f"Alpha error for {title!r}: {type(e).__name__}: {e}"
            errors.append(msg); log.error(msg); claims_a = []

        # ── Beta ───────────────────────────────────────────────────────────────
        try:
            claims_b = builder_b.process_work(source)
        except Exception as e:
            msg = f"Beta error for {title!r}: {type(e).__name__}: {e}"
            errors.append(msg); log.error(msg); claims_b = []

        # ── Diff ───────────────────────────────────────────────────────────────
        diff_report = diff_eng.compare(claims_a, claims_b, source_ref)
        total_diffs += len(diff_report.diffs)

        # ── Adjudicate ─────────────────────────────────────────────────────────
        adj = Adjudicator(claims_a, claims_b)
        adj_result = adj.adjudicate(diff_report)
        resolved = adj_result.resolved_claims

        all_resolved.extend(resolved)
        print(
            f"      [{len(resolved):3d} resolved]  {title} …  "
            f"(α={len(claims_a)} β={len(claims_b)} "
            f"diffs={len(diff_report.diffs)} "
            f"H={len(diff_report.high)} M={len(diff_report.medium)})"
        )

    diff_summary = {
        "total_diffs":    total_diffs,
        "works_processed": len(works),
    }
    return all_resolved, errors, diff_summary


# ── Ingest pipeline ────────────────────────────────────────────────────────────

def ingest(
    query:        str,
    max_results:  int,
    email:        str,
    from_year:    int | None,
    output:       str | None,
    verbose:      bool,
    demo:         bool = False,
    llm_key:      str  = "",
    llm_url:      str  = DEEPSEEK_API_URL,
    llm_model:    str  = DEEPSEEK_MODEL,
    llm_key_b:    str  = "",
    llm_url_b:    str  = OPENROUTER_API_URL,
    llm_model_b:  str  = OPENROUTER_MODEL,
) -> int:
    logging.basicConfig(
        level  = logging.DEBUG if verbose else logging.WARNING,
        format = "%(levelname)s %(name)s %(message)s",
    )

    use_llm  = bool(llm_key)
    use_dual = use_llm and bool(llm_key_b)
    mode     = "dual-LLM" if use_dual else ("single-LLM" if use_llm else ("demo" if demo else "rule-based"))

    print(f"\n[Alexandria Ingest]  query={query!r}  max={max_results}  mode={mode}")
    if from_year:
        print(f"                     from_year={from_year}")
    if use_llm:
        print(f"                     alpha: {llm_url}  model={llm_model}")
    if use_dual:
        print(f"                     beta:  {llm_url_b}  model={llm_model_b}")

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
            print(f"      ERROR: OpenAlex HTTP {e.response.status_code}", file=sys.stderr)
            return 1
        except httpx.ConnectError:
            print("      ERROR: Cannot connect to api.openalex.org — check network", file=sys.stderr)
            print("      TIP:   Use --demo for offline testing", file=sys.stderr)
            return 1
        print(f"      {len(works)} works retrieved.")

    # ── Step 2: Extract claims ─────────────────────────────────────────────────
    llm_errors:   list[str] = []
    diff_summary: dict      = {}

    if use_dual:
        print(f"\n[2/3] Dual-LLM extraction (Alpha + Beta → Diff → Adjudicate) …")
        raw_claims, llm_errors, diff_summary = dual_llm_extract(
            works,
            cfg_alpha = (llm_url,   llm_key,   llm_model),
            cfg_beta  = (llm_url_b, llm_key_b, llm_model_b),
        )
    elif use_llm:
        print(f"\n[2/3] Single-LLM extraction (Alpha only) …")
        raw_claims, llm_errors = single_llm_extract(works, llm_url, llm_key, llm_model)
    else:
        print(f"\n[2/3] Rule-based extraction …")
        raw_claims = []
        for work in works:
            wc = work_to_claims(work)
            raw_claims.extend(wc)
            print(f"      [{len(raw_claims):4d} claims]  {(work.get('title') or '')[:60]} …")

    if llm_errors:
        for e in llm_errors:
            print(f"      WARN: {e}", file=sys.stderr)

    # ── Step 3: Patch chain ────────────────────────────────────────────────────
    print(f"\n[3/3] Building patch chain …")
    chain        = PatchChain()
    emitter      = PatchEmitter(chain)
    all_claims:  list[ClaimNode] = []
    skip_errors: list[str]       = []

    for claim in raw_claims:
        try:
            time.sleep(0.001)
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
    for v in violations:
        print(f"      !! {v}", file=sys.stderr)

    # ── Report ─────────────────────────────────────────────────────────────────
    category_counts:  dict[str, int] = {}
    predicate_counts: dict[str, int] = {}
    for c in all_claims:
        category_counts[c.category.value]  = category_counts.get(c.category.value, 0)  + 1
        predicate_counts[c.predicate]       = predicate_counts.get(c.predicate, 0)       + 1

    report: dict = {
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
    if diff_summary:
        report["diff_summary"] = diff_summary

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
  default            Rule-based (deterministic, no LLM)
  --llm-key KEY      Single-LLM via DeepSeek (or any OpenAI-compatible endpoint)
  --llm-key-b KEY2   Dual-LLM: Alpha+Beta → Diff → Adjudicate → PatchChain
  --demo             Offline test with synthetic data

Examples:
  python openalex_ingest.py "climate change" --max 10
  python openalex_ingest.py "mRNA vaccines" --llm-key $DEEPSEEK_API_KEY --max 3
  python openalex_ingest.py "CRISPR" \\
      --llm-key  $DEEPSEEK_API_KEY \\
      --llm-key-b $OPENROUTER_API_KEY \\
      --llm-model-b "meta-llama/llama-3.1-8b-instruct" --max 3
  python openalex_ingest.py "any topic" --demo
""",
    )
    parser.add_argument("query",       help="OpenAlex full-text search query")
    parser.add_argument("--max",       type=int, default=10, dest="max_results",
                        help="Max papers to fetch (default: 10)")
    parser.add_argument("--email",     default="",
                        help="Email for OpenAlex polite pool")
    parser.add_argument("--from-year", type=int, default=None, dest="from_year",
                        help="Filter: only papers from this year onwards")
    parser.add_argument("--output",    default=None,
                        help="Save JSON report to file (default: print to stdout)")
    parser.add_argument("--verbose",   action="store_true")
    parser.add_argument("--demo",      action="store_true",
                        help="Use synthetic data (offline test)")

    alpha = parser.add_argument_group("Builder Alpha (primary LLM)")
    alpha.add_argument("--llm-key",   default=os.environ.get("DEEPSEEK_API_KEY", ""),
                       help="API key (or set DEEPSEEK_API_KEY env var)")
    alpha.add_argument("--llm-url",   default=DEEPSEEK_API_URL,
                       help=f"Base URL (default: {DEEPSEEK_API_URL})")
    alpha.add_argument("--llm-model", default=DEEPSEEK_MODEL,
                       help=f"Model (default: {DEEPSEEK_MODEL})")

    beta = parser.add_argument_group("Builder Beta (enables dual-LLM mode)")
    beta.add_argument("--llm-key-b",   default=os.environ.get("OPENROUTER_API_KEY", ""),
                      help="API key for Beta (or set OPENROUTER_API_KEY env var)")
    beta.add_argument("--llm-url-b",   default=OPENROUTER_API_URL,
                      help=f"Base URL for Beta (default: {OPENROUTER_API_URL})")
    beta.add_argument("--llm-model-b", default=OPENROUTER_MODEL,
                      help=f"Model for Beta (default: {OPENROUTER_MODEL})")

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
        llm_key_b   = args.llm_key_b,
        llm_url_b   = args.llm_url_b,
        llm_model_b = args.llm_model_b,
    ))


if __name__ == "__main__":
    main()
