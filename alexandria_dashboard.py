#!/usr/bin/env python3
"""
alexandria_dashboard.py — Alexandria Protocol Epistemic Graph Dashboard

Modern Streamlit UI for visualizing claims extracted from OpenAlex.

Run:
    streamlit run alexandria_dashboard.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import networkx as nx
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="Alexandria Protocol",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Load openalex_ingest module ───────────────────────────────────────────────
_DIR = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("openalex_ingest", _DIR / "openalex_ingest.py")
_oi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_oi)

# ── Global color scheme ───────────────────────────────────────────────────────
CAT_COLORS = {
    "EMPIRICAL":   "#3B82F6",   # blue
    "MODEL":       "#8B5CF6",   # violet
    "SPECULATIVE": "#F59E0B",   # amber
    "NORMATIVE":   "#10B981",   # emerald
}
PRED_COLORS = {
    "CONTRIBUTES_TO":   "#06B6D4",  # cyan
    "RELATES_TO":       "#6366F1",  # indigo
    "MENTIONS":         "#F59E0B",  # amber
    "CAUSES":           "#EC4899",  # pink
    "SUPPORTS":         "#22C55E",  # green
    "STRONGLY_SUPPORTS":"#16A34A",  # dark green
    "PARTIALLY_SUPPORTS":"#84CC16", # lime
    "CORRELATES_WITH":  "#EAB308",  # yellow
    "CONTRADICTS":      "#EF4444",  # red
    "REFINES":          "#A78BFA",  # purple-light
    "DERIVED_FROM":     "#FB923C",  # orange
    "EXTENDS":          "#38BDF8",  # sky
}
DEFAULT_PRED_COLOR = "#94A3B8"


# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Sidebar */
[data-testid="stSidebar"] {
    background: #0D1117;
    border-right: 1px solid #21262D;
}
/* Main background */
.stApp { background: #0D1117; }

/* Metric cards */
[data-testid="metric-container"] {
    background: #161B22;
    border: 1px solid #21262D;
    border-radius: 12px;
    padding: 1rem 1.25rem;
}
[data-testid="stMetricValue"] { font-size: 2rem !important; font-weight: 700; }

/* Section headers */
.section-header {
    font-size: 1rem;
    font-weight: 600;
    color: #8B949E;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin: 1.5rem 0 0.75rem;
    border-bottom: 1px solid #21262D;
    padding-bottom: 0.4rem;
}

/* Tag chips */
.chip {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 600;
    margin: 1px 2px;
}

/* Chain hash */
.chain-hash {
    font-family: monospace;
    font-size: 0.8rem;
    color: #58A6FF;
    background: #0D1117;
    padding: 4px 10px;
    border-radius: 6px;
    border: 1px solid #21262D;
}

/* Integrity badge */
.badge-ok  { color: #3FB950; background: #0F2A1A; border: 1px solid #2EA043;
             padding: 3px 12px; border-radius: 999px; font-weight: 700; font-size: 0.85rem; }
.badge-err { color: #F85149; background: #2D1115; border: 1px solid #DA3633;
             padding: 3px 12px; border-radius: 999px; font-weight: 700; font-size: 0.85rem; }
</style>
""", unsafe_allow_html=True)


# ── Core query runner (no stdout) ─────────────────────────────────────────────

def run_query(
    query:        str,
    max_results:  int,
    email:        str,
    from_year:    int | None,
    demo:         bool,
    llm_key:      str,
    llm_url:      str,
    llm_model:    str,
    llm_key_b:    str,
    llm_url_b:    str,
    llm_model_b:  str,
) -> dict:
    """
    Execute the ingest pipeline and return a result dict for the UI.
    Never prints — all status goes through st.status.
    """
    from alexandria_core.patch import PatchChain, PatchEmitter

    use_llm  = bool(llm_key)
    use_dual = use_llm and bool(llm_key_b)
    mode     = "dual-LLM" if use_dual else ("single-LLM" if use_llm else ("demo" if demo else "rule-based"))

    # ── Fetch ──────────────────────────────────────────────────────────────────
    if demo:
        works = _oi.demo_works(query, max_results)
    else:
        works = _oi.fetch_works(query, max_results, email, from_year)

    # ── Extract claims ─────────────────────────────────────────────────────────
    llm_errors:   list = []
    diff_summary: dict = {}

    if use_dual:
        raw_claims, llm_errors, diff_summary = _oi.dual_llm_extract(
            works,
            cfg_alpha=(_oi.DEEPSEEK_API_URL if not llm_url else llm_url,   llm_key,   llm_model),
            cfg_beta =(_oi.OPENROUTER_API_URL if not llm_url_b else llm_url_b, llm_key_b, llm_model_b),
        )
    elif use_llm:
        raw_claims, llm_errors = _oi.single_llm_extract(works, llm_url, llm_key, llm_model)
    else:
        raw_claims = []
        for work in works:
            raw_claims.extend(_oi.work_to_claims(work))

    # ── Patch chain ────────────────────────────────────────────────────────────
    chain   = PatchChain()
    emitter = PatchEmitter(chain)
    accepted: list = []
    skipped:  list = []

    for claim in raw_claims:
        try:
            time.sleep(0.001)
            emitter.add(claim)
            accepted.append(claim)
        except ValueError as e:
            skipped.append({"claim": claim.claim_id[:8], "error": str(e)})

    ok, violations = chain.verify_integrity()

    # ── Build result dict ──────────────────────────────────────────────────────
    cat_counts:  dict[str, int] = {}
    pred_counts: dict[str, int] = {}
    mod_counts:  dict[str, int] = {}

    claims_data = []
    for c in accepted:
        cat_counts[c.category.value]  = cat_counts.get(c.category.value, 0)  + 1
        pred_counts[c.predicate]       = pred_counts.get(c.predicate, 0)       + 1
        mod_counts[c.modality.value]   = mod_counts.get(c.modality.value, 0)   + 1
        claims_data.append({
            "id":          c.claim_id[:8] + "…",
            "subject":     c.subject[:80],
            "predicate":   c.predicate,
            "object":      c.object[:80],
            "category":    c.category.value,
            "modality":    c.modality.value,
            "status":      c.status.value,
            "source":      c.source_refs[0] if c.source_refs else "",
            "assumptions": len(c.assumptions),
        })

    return {
        "query":         query,
        "mode":          mode,
        "works_fetched": len(works),
        "claims_total":  len(accepted),
        "claims_skipped":len(skipped),
        "llm_errors":    len(llm_errors),
        "chain_length":  chain.length,
        "chain_head":    chain.head_hash[:32],
        "integrity_ok":  ok,
        "violations":    violations,
        "by_category":   cat_counts,
        "by_predicate":  pred_counts,
        "by_modality":   mod_counts,
        "diff_summary":  diff_summary,
        "claims":        claims_data,
        "works":         works,
        "llm_error_list":llm_errors,
    }


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_claim_graph(claims: list[dict], max_nodes: int = 80) -> go.Figure:
    """
    Build an interactive force-directed graph from claim triples.
    Nodes = subjects + objects, edges = predicates.
    """
    G = nx.DiGraph()
    cat_map:  dict[str, str] = {}   # node → category (for coloring)
    node_freq: dict[str, int] = {}  # node → degree count

    # Cap claims for readability
    sample = claims[:max_nodes * 2]

    for c in sample:
        s, p, o = c["subject"], c["predicate"], c["object"]
        G.add_edge(s, o, predicate=p, category=c["category"])
        cat_map.setdefault(s, c["category"])
        cat_map.setdefault(o, c["category"])
        node_freq[s] = node_freq.get(s, 0) + 1
        node_freq[o] = node_freq.get(o, 0) + 1

    if len(G.nodes) == 0:
        fig = go.Figure()
        fig.add_annotation(text="No claims to display", showarrow=False,
                           font=dict(size=16, color="#8B949E"), xref="paper", yref="paper", x=0.5, y=0.5)
        fig.update_layout(_graph_layout())
        return fig

    # Limit nodes to top by frequency
    if len(G.nodes) > max_nodes:
        top_nodes = sorted(node_freq, key=node_freq.get, reverse=True)[:max_nodes]
        G = G.subgraph(top_nodes).copy()

    pos = nx.spring_layout(G, seed=42, k=2.5 / (len(G.nodes) ** 0.5 + 1))

    # ── Edge traces (one per predicate type for legend) ───────────────────────
    edge_traces = {}
    for u, v, data in G.edges(data=True):
        pred = data.get("predicate", "RELATES_TO")
        color = PRED_COLORS.get(pred, DEFAULT_PRED_COLOR)
        if pred not in edge_traces:
            edge_traces[pred] = {"x": [], "y": [], "color": color}
        x0, y0 = pos[u]; x1, y1 = pos[v]
        edge_traces[pred]["x"] += [x0, x1, None]
        edge_traces[pred]["y"] += [y0, y1, None]

    traces = []
    for pred, ed in edge_traces.items():
        traces.append(go.Scatter(
            x=ed["x"], y=ed["y"],
            mode="lines",
            name=pred,
            line=dict(color=ed["color"], width=1.5),
            opacity=0.7,
            hoverinfo="none",
        ))

    # ── Node trace ────────────────────────────────────────────────────────────
    nx_nodes  = list(G.nodes)
    node_x    = [pos[n][0] for n in nx_nodes]
    node_y    = [pos[n][1] for n in nx_nodes]
    node_col  = [CAT_COLORS.get(cat_map.get(n, "MODEL"), "#8B5CF6") for n in nx_nodes]
    node_size = [12 + min(node_freq.get(n, 1) * 3, 30) for n in nx_nodes]
    node_text = [
        f"<b>{n[:60]}</b><br>Category: {cat_map.get(n,'?')}<br>Connections: {node_freq.get(n,0)}"
        for n in nx_nodes
    ]
    node_labels = [n[:30] + "…" if len(n) > 30 else n for n in nx_nodes]

    traces.append(go.Scatter(
        x=node_x, y=node_y,
        mode="markers+text",
        name="Nodes",
        marker=dict(
            color=node_col, size=node_size,
            line=dict(width=1.5, color="#0D1117"),
            symbol="circle",
        ),
        text=node_labels,
        textposition="top center",
        textfont=dict(size=9, color="#C9D1D9"),
        hovertext=node_text,
        hoverinfo="text",
        showlegend=False,
    ))

    fig = go.Figure(data=traces)
    fig.update_layout(
        **_graph_layout(),
        legend=dict(
            title="Predicates",
            bgcolor="#161B22",
            bordercolor="#21262D",
            borderwidth=1,
            font=dict(color="#C9D1D9", size=11),
        ),
    )
    return fig


def _graph_layout() -> dict:
    return dict(
        paper_bgcolor="#0D1117",
        plot_bgcolor="#0D1117",
        font=dict(color="#C9D1D9"),
        margin=dict(l=20, r=20, t=10, b=10),
        height=520,
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
    )


# ── Chart helpers ─────────────────────────────────────────────────────────────

def _dark_layout(**kwargs) -> dict:
    base = dict(
        paper_bgcolor="#161B22",
        plot_bgcolor="#161B22",
        font=dict(color="#C9D1D9", size=12),
        margin=dict(l=10, r=10, t=30, b=10),
    )
    base.update(kwargs)
    return base


def category_donut(by_cat: dict) -> go.Figure:
    labels = list(by_cat.keys())
    values = list(by_cat.values())
    colors = [CAT_COLORS.get(l, "#8B949E") for l in labels]
    fig = go.Figure(go.Pie(
        labels=labels, values=values,
        hole=0.55,
        marker=dict(colors=colors, line=dict(color="#0D1117", width=2)),
        textinfo="label+percent",
        textfont=dict(size=11),
        hovertemplate="<b>%{label}</b><br>%{value} claims (%{percent})<extra></extra>",
    ))
    fig.update_layout(**_dark_layout(height=280, showlegend=False,
                                      title=dict(text="By Category", font=dict(size=13))))
    return fig


def predicate_bar(by_pred: dict) -> go.Figure:
    sorted_items = sorted(by_pred.items(), key=lambda x: x[1], reverse=True)
    labels = [k for k, _ in sorted_items]
    values = [v for _, v in sorted_items]
    colors = [PRED_COLORS.get(l, DEFAULT_PRED_COLOR) for l in labels]
    fig = go.Figure(go.Bar(
        x=values, y=labels,
        orientation="h",
        marker=dict(color=colors, line=dict(color="#0D1117", width=1)),
        text=values, textposition="outside",
        textfont=dict(size=10, color="#C9D1D9"),
        hovertemplate="<b>%{y}</b>: %{x} claims<extra></extra>",
    ))
    fig.update_layout(**_dark_layout(
        height=280,
        title=dict(text="By Predicate", font=dict(size=13)),
        xaxis=dict(showgrid=True, gridcolor="#21262D"),
        yaxis=dict(showgrid=False),
    ))
    return fig


def modality_bar(by_mod: dict) -> go.Figure:
    order = ["hypothesis", "suggestion", "evidence", "established"]
    labels = [m for m in order if m in by_mod] + [m for m in by_mod if m not in order]
    values = [by_mod.get(l, 0) for l in labels]
    mod_colors = {
        "hypothesis":   "#F59E0B",
        "suggestion":   "#6366F1",
        "evidence":     "#3B82F6",
        "established":  "#10B981",
    }
    colors = [mod_colors.get(l, DEFAULT_PRED_COLOR) for l in labels]
    fig = go.Figure(go.Bar(
        x=labels, y=values,
        marker=dict(color=colors, line=dict(color="#0D1117", width=1)),
        text=values, textposition="outside",
        textfont=dict(size=10, color="#C9D1D9"),
        hovertemplate="<b>%{x}</b>: %{y} claims<extra></extra>",
    ))
    fig.update_layout(**_dark_layout(
        height=280,
        title=dict(text="By Modality", font=dict(size=13)),
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="#21262D"),
    ))
    return fig


# ── Source timeline ───────────────────────────────────────────────────────────

def source_timeline(works: list[dict]) -> go.Figure:
    year_counts: dict[int, int] = {}
    for w in works:
        yr = w.get("publication_year")
        if yr:
            year_counts[yr] = year_counts.get(yr, 0) + 1
    if not year_counts:
        return None
    years  = sorted(year_counts)
    counts = [year_counts[y] for y in years]
    fig = go.Figure(go.Scatter(
        x=years, y=counts,
        mode="lines+markers",
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.15)",
        line=dict(color="#3B82F6", width=2),
        marker=dict(color="#3B82F6", size=7),
        hovertemplate="<b>%{x}</b>: %{y} papers<extra></extra>",
    ))
    fig.update_layout(**_dark_layout(
        height=200,
        title=dict(text="Papers by Year", font=dict(size=13)),
        xaxis=dict(showgrid=False, type="linear"),
        yaxis=dict(showgrid=True, gridcolor="#21262D"),
    ))
    return fig


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 📚 Alexandria Protocol")
    st.markdown("<div style='color:#8B949E;font-size:0.8rem;margin-bottom:1.2rem;'>Epistemic Graph Builder</div>", unsafe_allow_html=True)

    st.markdown("#### Query")
    query = st.text_input("Search query", value="mRNA vaccines", label_visibility="collapsed",
                          placeholder="e.g. climate change, CRISPR, mRNA vaccines")
    max_results = st.slider("Max papers", 1, 50, 10)
    from_year   = st.number_input("From year (optional)", min_value=1990, max_value=2026,
                                  value=None, step=1, format="%d")
    email       = st.text_input("Email (OpenAlex polite pool)", value="", placeholder="you@example.com")

    st.markdown("---")
    st.markdown("#### Extraction mode")
    mode_choice = st.radio(
        "Mode",
        ["Rule-based (no LLM)", "Single LLM", "Dual LLM (full DBA)", "Demo (offline)"],
        label_visibility="collapsed",
    )

    llm_key = llm_key_b = ""
    llm_url = _oi.DEEPSEEK_API_URL
    llm_url_b = _oi.OPENROUTER_API_URL
    llm_model = _oi.DEEPSEEK_MODEL
    llm_model_b = _oi.OPENROUTER_MODEL

    if mode_choice in ("Single LLM", "Dual LLM (full DBA)"):
        st.markdown("**Builder Alpha**")
        llm_key   = st.text_input("API Key Alpha", type="password",
                                   value=os.environ.get("DEEPSEEK_API_KEY", ""),
                                   placeholder="sk-…")
        llm_url   = st.text_input("Endpoint Alpha", value=_oi.DEEPSEEK_API_URL)
        llm_model = st.text_input("Model Alpha", value=_oi.DEEPSEEK_MODEL)

    if mode_choice == "Dual LLM (full DBA)":
        st.markdown("**Builder Beta**")
        llm_key_b   = st.text_input("API Key Beta", type="password", placeholder="sk-or-…")
        llm_url_b   = st.text_input("Endpoint Beta", value=_oi.OPENROUTER_API_URL)
        llm_model_b = st.text_input("Model Beta", value=_oi.OPENROUTER_MODEL)

    st.markdown("---")
    st.markdown("#### Graph")
    max_graph_nodes = st.slider("Max graph nodes", 20, 200, 80)

    st.markdown("---")
    run_btn = st.button("▶  Run Ingest", type="primary", use_container_width=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<div style='color:#8B949E;font-size:0.75rem;'>Alexandria Protocol v2.2 · Rule-based + LLM · SHA-256 Chain</div>",
                unsafe_allow_html=True)


if mode_choice in ("Single LLM", "Dual LLM (full DBA)") and not llm_key:
    llm_key = os.environ.get("DEEPSEEK_API_KEY", "")
if mode_choice == "Dual LLM (full DBA)" and not llm_key_b:
    llm_key_b = os.environ.get("OPENROUTER_API_KEY", "")


# ── Main area ─────────────────────────────────────────────────────────────────

st.markdown(
    "<h1 style='font-size:1.8rem;font-weight:700;color:#E6EDF3;margin-bottom:0;'>"
    "📚 Alexandria Epistemic Graph"
    "</h1>"
    "<div style='color:#8B949E;font-size:0.9rem;margin-bottom:1.5rem;'>"
    "Extract, link and verify epistemic claims from scientific literature."
    "</div>",
    unsafe_allow_html=True,
)

# Init session state
if "result" not in st.session_state:
    st.session_state.result = None

# ── Run ───────────────────────────────────────────────────────────────────────
if run_btn:
    is_demo = (mode_choice == "Demo (offline)")
    with st.status(f"Running ingest — mode: **{mode_choice}**", expanded=True) as status:
        try:
            st.write(f"Fetching papers for: *{query}*")
            t0 = time.time()
            result = run_query(
                query=query,
                max_results=max_results,
                email=email,
                from_year=int(from_year) if from_year else None,
                demo=is_demo,
                llm_key=llm_key,
                llm_url=llm_url,
                llm_model=llm_model,
                llm_key_b=llm_key_b,
                llm_url_b=llm_url_b,
                llm_model_b=llm_model_b,
            )
            elapsed = time.time() - t0
            st.session_state.result = result
            st.write(f"✅ Done in **{elapsed:.1f}s** — "
                     f"{result['works_fetched']} papers, "
                     f"{result['claims_total']} claims, "
                     f"{result['chain_length']} patches")
            status.update(label="Ingest complete", state="complete", expanded=False)
        except Exception as e:
            status.update(label=f"Error: {e}", state="error")
            st.error(str(e))

# ── Render result ─────────────────────────────────────────────────────────────
result = st.session_state.result

if result is None:
    st.markdown(
        "<div style='text-align:center;margin-top:5rem;color:#8B949E;'>"
        "<div style='font-size:3rem;'>🔬</div>"
        "<div style='font-size:1.1rem;margin-top:0.5rem;'>Configure a query in the sidebar and press <b>Run Ingest</b></div>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.stop()

# ── Metrics row ───────────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Papers", result["works_fetched"])
c2.metric("Claims accepted", result["claims_total"])
c3.metric("Claims skipped", result["claims_skipped"],
          delta=None if result["claims_skipped"] == 0 else f"-{result['claims_skipped']}", delta_color="inverse")
c4.metric("Patch chain", result["chain_length"])
with c5:
    st.metric("Chain integrity",
              "✅ OK" if result["integrity_ok"] else "❌ FAILED",
              label_visibility="visible")

# Mode + chain info
col_l, col_r = st.columns([2, 1])
with col_l:
    st.markdown(
        f"<div style='color:#8B949E;font-size:0.82rem;margin-top:-0.5rem;'>"
        f"Mode: <b style='color:#C9D1D9;'>{result['mode']}</b> &nbsp;·&nbsp; "
        f"Query: <b style='color:#C9D1D9;'>{result['query']}</b>"
        f"</div>",
        unsafe_allow_html=True,
    )
with col_r:
    st.markdown(
        f"<div style='text-align:right;font-size:0.78rem;color:#8B949E;'>"
        f"Chain head: <span class='chain-hash'>{result['chain_head'][:24]}…</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

if result["violations"]:
    with st.expander(f"⚠️ {len(result['violations'])} chain violation(s)", expanded=True):
        for v in result["violations"]:
            st.code(v)

if result["llm_error_list"]:
    with st.expander(f"⚠️ {len(result['llm_error_list'])} LLM error(s)"):
        for e in result["llm_error_list"]:
            st.text(e)

st.markdown("---")

# ── Network graph ─────────────────────────────────────────────────────────────
st.markdown("<div class='section-header'>Claim Network Graph</div>", unsafe_allow_html=True)

graph_filter_col, _ = st.columns([3, 1])
with graph_filter_col:
    filter_cats = st.multiselect(
        "Filter by category",
        options=list(CAT_COLORS.keys()),
        default=list(result["by_category"].keys()),
        key="graph_filter",
    )
filtered_claims = [c for c in result["claims"] if c["category"] in filter_cats]
fig_graph = build_claim_graph(filtered_claims, max_nodes=max_graph_nodes)
st.plotly_chart(fig_graph, use_container_width=True, config={"displayModeBar": False})

# ── Charts row ────────────────────────────────────────────────────────────────
st.markdown("<div class='section-header'>Epistemic Statistics</div>", unsafe_allow_html=True)

ch1, ch2, ch3 = st.columns(3)
with ch1:
    if result["by_category"]:
        st.plotly_chart(category_donut(result["by_category"]),
                        use_container_width=True, config={"displayModeBar": False})
with ch2:
    if result["by_predicate"]:
        st.plotly_chart(predicate_bar(result["by_predicate"]),
                        use_container_width=True, config={"displayModeBar": False})
with ch3:
    if result["by_modality"]:
        st.plotly_chart(modality_bar(result["by_modality"]),
                        use_container_width=True, config={"displayModeBar": False})

# ── Timeline ──────────────────────────────────────────────────────────────────
timeline_fig = source_timeline(result.get("works", []))
if timeline_fig:
    st.markdown("<div class='section-header'>Source Timeline</div>", unsafe_allow_html=True)
    st.plotly_chart(timeline_fig, use_container_width=True, config={"displayModeBar": False})

# ── Claims table ──────────────────────────────────────────────────────────────
st.markdown("<div class='section-header'>Claims Table</div>", unsafe_allow_html=True)

tbl_col1, tbl_col2, tbl_col3 = st.columns([2, 2, 2])
with tbl_col1:
    cat_filter = st.multiselect("Category", options=list(CAT_COLORS.keys()),
                                default=list(result["by_category"].keys()), key="tbl_cat")
with tbl_col2:
    pred_filter = st.multiselect("Predicate", options=list(result["by_predicate"].keys()),
                                 default=list(result["by_predicate"].keys()), key="tbl_pred")
with tbl_col3:
    search_text = st.text_input("Search subject/object", placeholder="filter text…", key="tbl_search")

tbl_data = [
    c for c in result["claims"]
    if c["category"] in cat_filter
    and c["predicate"] in pred_filter
    and (not search_text or search_text.lower() in c["subject"].lower() or search_text.lower() in c["object"].lower())
]

if tbl_data:
    st.dataframe(
        tbl_data,
        use_container_width=True,
        height=min(400, 40 + len(tbl_data) * 35),
        column_config={
            "id":          st.column_config.TextColumn("ID", width=90),
            "subject":     st.column_config.TextColumn("Subject", width=220),
            "predicate":   st.column_config.TextColumn("Predicate", width=160),
            "object":      st.column_config.TextColumn("Object", width=220),
            "category":    st.column_config.TextColumn("Category", width=110),
            "modality":    st.column_config.TextColumn("Modality", width=110),
            "status":      st.column_config.TextColumn("Status", width=110),
            "assumptions": st.column_config.NumberColumn("Assumptions", width=90),
            "source":      st.column_config.TextColumn("Source", width=200),
        },
        hide_index=True,
    )
    st.caption(f"{len(tbl_data)} of {result['claims_total']} claims shown")
else:
    st.info("No claims match the current filter.")

# ── Export ────────────────────────────────────────────────────────────────────
st.markdown("---")
export_col, _ = st.columns([1, 3])
with export_col:
    export_data = {k: v for k, v in result.items() if k != "works"}
    st.download_button(
        "⬇  Export JSON",
        data=json.dumps(export_data, indent=2, ensure_ascii=False),
        file_name=f"alexandria_{result['query'][:30].replace(' ', '_')}.json",
        mime="application/json",
        use_container_width=True,
    )
