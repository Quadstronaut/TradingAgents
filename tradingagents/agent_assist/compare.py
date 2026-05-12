"""Head-to-head compare flow.

Runs the full deep pipeline on each ticker sequentially (via the
orchestrator's ``_run_one_deep``), then calls a structured-output LLM to
produce a one-paragraph comparison verdict. Outputs a markdown summary
listing both ratings and the verdict.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field

from tradingagents.agent_assist.config import (
    ANALYSIS_BASE_URL,
    ANALYSIS_PROVIDER,
    ANALYSIS_QUICK_MODEL,
)
from tradingagents.agent_assist.summarize import RunResult
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.llm_clients import create_llm_client

logger = logging.getLogger(__name__)


class ComparisonVerdict(BaseModel):
    """LLM output for the compare flow."""
    winner: Literal["A", "B", "tie"] = Field(
        description="Which ticker is the better pick now, or 'tie' if they're equivalent."
    )
    reasoning: str = Field(
        description="One paragraph (3-5 sentences) explaining the call.",
    )


def _build_comparison_llm():
    return create_llm_client(
        provider=ANALYSIS_PROVIDER,
        model=ANALYSIS_QUICK_MODEL,
        base_url=ANALYSIS_BASE_URL,
    ).get_llm().with_structured_output(ComparisonVerdict)


def _summarize_comparison(a: RunResult, b: RunResult) -> ComparisonVerdict:
    """Ask the quick model which one is the better pick."""
    llm = _build_comparison_llm()
    prompt = (
        "You are an investment committee chair. Two tickers have been analysed by "
        "your team. Each has a final rating and a decision write-up. Compare them "
        "head-to-head and decide which is the better pick to add to a portfolio now, "
        "or call it a tie if they are essentially equivalent. Be specific about why.\n\n"
        f"--- Ticker A: {a.ticker} (rating: {a.rating}) ---\n"
        f"{a.decision_md or '(no decision available)'}\n\n"
        f"--- Ticker B: {b.ticker} (rating: {b.rating}) ---\n"
        f"{b.decision_md or '(no decision available)'}\n"
    )
    return llm.invoke(prompt)


DeepRunner = Callable[..., RunResult]
SummaryWriter = Callable[..., Path]


def run_compare(
    ticker_a: str,
    ticker_b: str,
    *,
    output_dir: Path,
    deep_runner: DeepRunner,
    summary_writer: SummaryWriter,
) -> int:
    """Run deep on both tickers and write a comparison summary.

    ``deep_runner`` and ``summary_writer`` are injected to avoid a circular
    import with the orchestrator module.
    """
    today = datetime.date.today().isoformat()

    print(f"=== Compare: {ticker_a} vs {ticker_b} ===")
    print(f"Running deep analysis on {ticker_a} first (~15 min)…")
    result_a = deep_runner(ticker_a, today=today)
    print(f"Running deep analysis on {ticker_b} (~15 min)…")
    result_b = deep_runner(ticker_b, today=today)

    if result_a.rating == "FAILED" or result_b.rating == "FAILED":
        print("One or both deep runs failed; writing partial summary.")
        verdict = ComparisonVerdict(
            winner="tie",
            reasoning="One or both deep runs failed; verdict not available.",
        )
    else:
        try:
            verdict = _summarize_comparison(result_a, result_b)
        except Exception as exc:
            logger.exception("comparison summariser failed")
            verdict = ComparisonVerdict(
                winner="tie",
                reasoning=f"Comparison summariser failed: {exc}",
            )

    winner_ticker = (
        ticker_a if verdict.winner == "A"
        else ticker_b if verdict.winner == "B"
        else None
    )

    # Build a comparison-aware summary alongside the regular ranked summary.
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    # Per CLAUDE.md: ticker components in paths must go through safe_ticker_component.
    safe_a = safe_ticker_component(ticker_a)
    safe_b = safe_ticker_component(ticker_b)
    out = output_dir / f"{ts}-compare-{safe_a}-vs-{safe_b}.md"

    pick_line = (
        f"**Pick:** {winner_ticker}" if winner_ticker
        else "**Pick:** tie / no clear winner"
    )

    body_parts = [
        f"# Compare — {ticker_a} vs {ticker_b} — {ts}",
        "",
        "## Head-to-head verdict",
        "",
        pick_line,
        "",
        verdict.reasoning,
        "",
        "## Individual ratings",
        "",
        f"- **{ticker_a}**: {result_a.rating}",
        f"- **{ticker_b}**: {result_b.rating}",
        "",
        "## Per-ticker decision detail",
        "",
        f"### {ticker_a}",
        "",
        result_a.decision_md or "_(no decision)_",
        "",
        f"### {ticker_b}",
        "",
        result_b.decision_md or "_(no decision)_",
        "",
    ]
    out.write_text("\n".join(body_parts), encoding="utf-8")

    # Also write the standard ranked summary for consistency with other flows.
    summary_writer(
        prompt=f"compare: {ticker_a} vs {ticker_b}",
        results=[result_a, result_b],
        output_dir=output_dir,
    )

    print()
    print(f"=== Summary written: {out} ===")
    print(f"  {ticker_a:<8} {result_a.rating}")
    print(f"  {ticker_b:<8} {result_b.rating}")
    if winner_ticker:
        print(f"  pick: {winner_ticker}")
    # Match the failure-mode contract of _run_specific / _run_owned /
    # _run_freeform: return 4 when one or both deep runs failed so the
    # process exit code reflects reality. The summary file is still
    # written with the partial results — exit code reflects success
    # of the analysis, not of the write.
    if result_a.rating == "FAILED" or result_b.rating == "FAILED":
        return 4
    return 0
