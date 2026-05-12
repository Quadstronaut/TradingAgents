"""Lightweight news & sentiment scan flow (~5 min).

Builds a stripped-down LangGraph: Social Analyst → News Analyst → Verdict.
The Verdict node uses structured output to emit a typed
:class:`NewsVerdict` (lean + confidence + summary). No PM, no Buy/Hold/Sell
rating — output is explicitly framed as "a read, not a recommendation".
"""

from __future__ import annotations

import contextlib
import datetime
import logging
from pathlib import Path
from typing import Iterator, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from tradingagents.agents import (
    AgentState,
    create_msg_delete,
    create_news_analyst,
    create_social_media_analyst,
)
from tradingagents.agents.utils.agent_utils import (
    get_global_news,
    get_insider_transactions,
    get_news,
    get_reddit_sentiment,
)
from tradingagents.agent_assist.config import (
    ANALYSIS_BASE_URL,
    ANALYSIS_DEEP_MODEL,
    ANALYSIS_PROVIDER,
    ANALYSIS_QUICK_MODEL,
)
from tradingagents.agent_assist.progress import (
    Phase,
    ProgressState,
    news_scan_phases,
    progress_display,
)
from tradingagents.dataflows.config import get_config, set_config
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.propagation import Propagator
from tradingagents.llm_clients import create_llm_client

logger = logging.getLogger(__name__)


class NewsVerdict(BaseModel):
    """Typed verdict for the news-scan flow.

    Deliberately not a Buy/Hold/Sell rating — this flow skips the debate
    and risk stages that the full pipeline uses to produce one.
    """
    lean: Literal["bullish", "neutral", "bearish"] = Field(
        description="Overall lean of the recent news and social discussion."
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Your confidence in the lean, 0 = guessing, 1 = clear signal.",
    )
    summary: str = Field(
        description="One paragraph synthesising what people are saying and what's in the news.",
    )


def _render_verdict(verdict: NewsVerdict, ticker: str) -> str:
    """Single source of truth for the verdict markdown layout.

    Used by the Verdict node to populate ``final_trade_decision`` on graph
    state, and indirectly by the file-output assembly. Keep the rendered
    layout stable here — the typed verdict is captured separately so the
    terminal tail summary does not parse this string.
    """
    return (
        f"## News & sentiment scan for {ticker}\n\n"
        f"**Lean:** {verdict.lean}  ·  **Confidence:** {verdict.confidence:.2f}\n\n"
        f"{verdict.summary}\n\n"
        "_This is a read, not a buy/hold/sell recommendation._"
    )


def _create_verdict_node(deep_llm, capture: list):
    """Verdict node: read the two reports, emit a structured verdict.

    Appends the typed :class:`NewsVerdict` to ``capture`` so the caller can
    read it back without parsing the rendered markdown. LangGraph's
    AgentState TypedDict doesn't declare a slot for the typed object and
    we don't want to pollute the shared schema for one flow's use.
    """
    structured = deep_llm.with_structured_output(NewsVerdict)

    def verdict_node(state):
        ticker = state["company_of_interest"]
        sentiment_report = state.get("sentiment_report", "") or "(none)"
        news_report = state.get("news_report", "") or "(none)"
        prompt = (
            "You are an investment news synthesiser. Based on the two reports below, "
            f"emit a one-paragraph verdict for {ticker} on whether the public/news "
            "lean is bullish, neutral, or bearish. This is NOT a buy/hold/sell "
            "recommendation — it is a read of what people are saying and what is in "
            "the news. Include your confidence in [0, 1]."
            "\n\n"
            "Social/sentiment report:\n"
            f"{sentiment_report}\n\n"
            "News report:\n"
            f"{news_report}\n"
        )
        verdict: NewsVerdict = structured.invoke(prompt)
        capture.append(verdict)
        return {
            "messages": state["messages"],
            "final_trade_decision": _render_verdict(verdict, ticker),
        }

    return verdict_node


def _build_graph(quick_llm, deep_llm, capture: list):
    cond = ConditionalLogic()

    social = create_social_media_analyst(quick_llm)
    news = create_news_analyst(quick_llm)
    verdict = _create_verdict_node(deep_llm, capture)
    clear_s = create_msg_delete()
    clear_n = create_msg_delete()

    social_tools = ToolNode([get_news, get_reddit_sentiment])
    news_tools = ToolNode([get_news, get_global_news, get_insider_transactions])

    g = StateGraph(AgentState)
    g.add_node("Social Analyst", social)
    g.add_node("Msg Clear Social", clear_s)
    g.add_node("tools_social", social_tools)
    g.add_node("News Analyst", news)
    g.add_node("Msg Clear News", clear_n)
    g.add_node("tools_news", news_tools)
    g.add_node("Verdict", verdict)

    g.add_edge(START, "Social Analyst")
    g.add_conditional_edges(
        "Social Analyst",
        cond.should_continue_social,
        ["tools_social", "Msg Clear Social"],
    )
    g.add_edge("tools_social", "Social Analyst")
    g.add_edge("Msg Clear Social", "News Analyst")
    g.add_conditional_edges(
        "News Analyst",
        cond.should_continue_news,
        ["tools_news", "Msg Clear News"],
    )
    g.add_edge("tools_news", "News Analyst")
    g.add_edge("Msg Clear News", "Verdict")
    g.add_edge("Verdict", END)

    return g.compile()


def _build_llms():
    quick = create_llm_client(
        provider=ANALYSIS_PROVIDER,
        model=ANALYSIS_QUICK_MODEL,
        base_url=ANALYSIS_BASE_URL,
    ).get_llm()
    deep = create_llm_client(
        provider=ANALYSIS_PROVIDER,
        model=ANALYSIS_DEEP_MODEL,
        base_url=ANALYSIS_BASE_URL,
    ).get_llm()
    return quick, deep


def _build_dataflow_config() -> dict:
    """Compose the dataflow config that analyst tools (get_news, get_reddit_sentiment,
    etc.) need to read during ``graph.stream``.

    All keys touched here are already in DEFAULT_CONFIG, so set_config's merge
    semantics are sufficient and the snapshot/restore in
    :func:`_scoped_dataflow_config` is a complete round-trip.
    """
    cfg = DEFAULT_CONFIG.copy()
    cfg["llm_provider"] = ANALYSIS_PROVIDER
    cfg["backend_url"] = ANALYSIS_BASE_URL
    cfg["deep_think_llm"] = ANALYSIS_DEEP_MODEL
    cfg["quick_think_llm"] = ANALYSIS_QUICK_MODEL
    return cfg


@contextlib.contextmanager
def _scoped_dataflow_config(cfg: dict) -> Iterator[None]:
    """Temporarily install ``cfg`` as the dataflow global config.

    The analyst tools read vendor routing and provider info from
    ``tradingagents.dataflows.config``'s module-level state. news_scan is a
    function-scoped flow — restoring the prior state on exit avoids leaking
    these settings into subsequent tasks in the same process (the menu
    loop runs tasks back-to-back without re-initialising the process).
    """
    prior = get_config()
    try:
        set_config(cfg)
        yield
    finally:
        set_config(prior)


def run_news_scan(ticker: str, *, output_dir: Path) -> int:
    """Execute the news-scan flow with live progress; write a summary."""
    today = datetime.date.today().isoformat()
    quick, deep = _build_llms()
    captured: list[NewsVerdict] = []
    graph = _build_graph(quick, deep, captured)
    cfg = _build_dataflow_config()

    state = ProgressState(
        ticker=ticker,
        label="news & sentiment scan",
        phases=news_scan_phases(),
        estimated_total_sec=5 * 60,
    )

    propagator = Propagator()
    init_state = propagator.create_initial_state(ticker, today)
    args = propagator.get_graph_args()
    args = {**args, "stream_mode": ["values", "updates"]}

    final_state: dict = {}
    try:
        with _scoped_dataflow_config(cfg), progress_display(state) as ps:
            for mode, payload in graph.stream(init_state, **args):
                if mode == "values":
                    final_state = payload
                elif mode == "updates" and isinstance(payload, dict):
                    for node_name, delta in payload.items():
                        ps.on_node_event(node_name, delta or {})
            ps.finish()
    except KeyboardInterrupt:
        raise

    verdict_md = final_state.get("final_trade_decision", "")
    # Persist a one-file summary alongside the regular agent_assist outputs.
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = output_dir / f"{ts}-news-scan-{ticker}.md"
    body = "\n".join([
        f"# News scan — {ticker} — {ts}",
        "",
        verdict_md or "_(no verdict produced)_",
        "",
        "## Source reports",
        "",
        "### Sentiment / social",
        "",
        final_state.get("sentiment_report", "_(empty)_"),
        "",
        "### News",
        "",
        final_state.get("news_report", "_(empty)_"),
        "",
    ])
    out.write_text(body, encoding="utf-8")

    print()
    print(f"=== Summary written: {out} ===")
    if captured:
        v = captured[-1]
        print(f"  {ticker:<8} {v.lean} (confidence {v.confidence:.2f})")
    elif verdict_md:
        # Verdict produced but the closure capture path didn't run (shouldn't
        # happen unless the graph short-circuited the Verdict node). The
        # markdown file is on disk; just don't try to summarise the tail.
        print(f"  {ticker:<8} (verdict written; see file)")
    return 0
