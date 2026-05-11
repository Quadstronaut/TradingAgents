"""Orchestrator: route a Task to the right flow, wire progress display, summarise.

Entry points:
    - ``run_task(task)`` — primary entry. The menu (or a scripted caller)
      hands in a Task; this routes per ``task.intent`` to a concrete flow.
    - ``main(prompt, budget)`` — back-compat wrapper for the old
      ``scripts/agent_assist.py --prompt ...`` shape; constructs a Task
      with ``intent='freeform'`` and delegates to ``run_task``.

All flows write a markdown summary under ``output_dir`` and return a
process exit code.
"""

from __future__ import annotations

import datetime
import logging
import re
from importlib.resources import files
from pathlib import Path
from typing import Optional

import pandas as pd

from tradingagents.agent_assist.config import (
    ANALYSIS_BASE_URL,
    ANALYSIS_DEEP_MODEL,
    ANALYSIS_PROVIDER,
    ANALYSIS_QUICK_MODEL,
)
from tradingagents.agent_assist.menu import Task
from tradingagents.agent_assist.progress import (
    ProgressState,
    full_pipeline_phases,
    progress_display,
)
from tradingagents.agent_assist.prompt_parse import ParsedPrompt, parse_prompt
from tradingagents.agent_assist.shortlist import PricedCandidate, shortlist
from tradingagents.agent_assist.summarize import RunResult, write_summary
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

logger = logging.getLogger(__name__)


_RATING_RE = re.compile(
    r"FINAL TRANSACTION PROPOSAL:\s*\*\*\s*(Buy|Overweight|Hold|Underweight|Sell)\s*\*\*",
    re.IGNORECASE,
)
_VALID_RATINGS = {"Buy", "Overweight", "Hold", "Underweight", "Sell"}

# Per-deep-run estimate used to populate the progress "est. remaining" hint.
DEEP_RUN_SECONDS = 15 * 60


def load_universe() -> pd.DataFrame:
    """Load the bundled ticker universe CSV."""
    path = files("tradingagents.agent_assist.data").joinpath("ticker_universe.csv")
    with path.open("r", encoding="utf-8") as f:
        return pd.read_csv(f)


def _confirm_run(ticker: str) -> str:
    """Returns 'y', 's', or 'a'."""
    while True:
        raw = input(f"Run deep analysis on {ticker} (~15 min)? [y]es / [s]kip / [a]bort: ").strip().lower()
        if raw in ("y", "yes"):
            return "y"
        if raw in ("s", "skip"):
            return "s"
        if raw in ("a", "abort"):
            return "a"
        print("Please answer y, s, or a.")


def _extract_rating(decision_md: str) -> str:
    m = _RATING_RE.search(decision_md or "")
    if m:
        cap = m.group(1).capitalize()
        if cap in _VALID_RATINGS:
            return cap
    for r in _VALID_RATINGS:
        if f"**{r}**" in (decision_md or ""):
            return r
    return "Hold"


def _build_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    cfg["llm_provider"] = ANALYSIS_PROVIDER
    cfg["backend_url"] = ANALYSIS_BASE_URL
    cfg["deep_think_llm"] = ANALYSIS_DEEP_MODEL
    cfg["quick_think_llm"] = ANALYSIS_QUICK_MODEL
    cfg["max_debate_rounds"] = 1
    cfg["max_risk_discuss_rounds"] = 1
    return cfg


# ---------------------------------------------------------------------------
# Single deep run with progress display
# ---------------------------------------------------------------------------


def _run_one_deep(
    ticker: str,
    *,
    today: str,
    position_str: str = "",
    config: Optional[dict] = None,
    label: str = "full deep analysis",
) -> RunResult:
    """Run the full pipeline on one ticker, with live progress, returning a RunResult.

    Exceptions are caught and folded into a FAILED RunResult so the caller
    can keep going with other tickers in a multi-ticker run.
    """
    config = config or _build_config()
    state = ProgressState(
        ticker=ticker,
        label=label,
        phases=full_pipeline_phases(),
        estimated_total_sec=DEEP_RUN_SECONDS,
    )
    try:
        with progress_display(state) as ps:
            ta = TradingAgentsGraph(
                debug=False,
                config=config,
                progress_callback=ps.on_node_event,
            )
            _, decision = ta.propagate(
                ticker,
                today,
                additional_portfolio_context=position_str,
            )
            ps.finish()
        rating = _extract_rating(decision)
        log_dir = Path(config["results_dir"]) / ticker
        print(f"[done] {ticker} -> {rating}")
        return RunResult(
            ticker=ticker, rating=rating, log_path=log_dir, error=None,
            decision_md=decision,
        )
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        logger.exception("propagate failed for %s", ticker)
        return RunResult(ticker=ticker, rating="FAILED", log_path=None, error=str(exc))


# ---------------------------------------------------------------------------
# Multi-ticker (shortlist) flow
# ---------------------------------------------------------------------------


def _print_shortlist(candidates: list[PricedCandidate]) -> None:
    print()
    print(f"=== Shortlist ({len(candidates)} candidates) ===")
    for c in candidates:
        price_str = f" @ ${c.last_price:.2f}" if c.last_price else ""
        print(f"  - {c.ticker}{price_str} - {c.reasoning}")
    est_min = len(candidates) * 15
    print(f"Estimated time if you run all: ~{est_min} min.")
    print()


def _run_shortlist_flow(
    *,
    seed_prompt: str,
    budget: Optional[int],
    universe_df: pd.DataFrame,
    output_dir: Path,
    position_first_str: str = "",
    summary_prompt_label: Optional[str] = None,
) -> int:
    """Shared multi-ticker flow: shortlist → confirm each → deep run → summary."""
    candidates = shortlist(seed_prompt, universe_df, budget=budget)
    if not candidates:
        print("No candidates to analyze. Try a more specific prompt.")
        return 3

    _print_shortlist(candidates)

    results: list[RunResult] = []
    config = _build_config()
    today = datetime.date.today().isoformat()

    for i, c in enumerate(candidates):
        choice = _confirm_run(c.ticker)
        if choice == "s":
            results.append(RunResult(ticker=c.ticker, rating="SKIPPED", log_path=None, error=None))
            continue
        if choice == "a":
            print(f"Aborting before {c.ticker}.")
            break

        # Position context only flows into the first ticker (legacy behaviour).
        pos = position_first_str if i == 0 else ""
        result = _run_one_deep(c.ticker, today=today, position_str=pos, config=config)
        results.append(result)

    summary_path = write_summary(
        prompt=summary_prompt_label or seed_prompt,
        results=results,
        output_dir=output_dir,
    )
    _print_summary_tail(summary_path, results)
    return 0


def _print_summary_tail(summary_path: Path, results: list[RunResult]) -> None:
    print()
    print(f"=== Summary written: {summary_path} ===")
    for r in results:
        print(f"  {r.ticker:<8} {r.rating}")


# ---------------------------------------------------------------------------
# Per-intent flows
# ---------------------------------------------------------------------------


def _run_specific(task: Task, *, output_dir: Path) -> int:
    today = datetime.date.today().isoformat()
    result = _run_one_deep(task.ticker, today=today)
    summary_path = write_summary(
        prompt=f"specific: {task.ticker}",
        results=[result],
        output_dir=output_dir,
    )
    _print_summary_tail(summary_path, [result])
    return 0 if result.rating != "FAILED" else 4


def _run_owned(task: Task, *, output_dir: Path) -> int:
    today = datetime.date.today().isoformat()
    pos = (
        f"User currently holds {task.shares:g} shares of {task.ticker} "
        f"at ${task.cost_basis:.2f} cost basis."
    )
    print(f"[position] {pos}")
    result = _run_one_deep(task.ticker, today=today, position_str=pos)
    summary_path = write_summary(
        prompt=f"owned: {task.ticker} ({task.shares:g}sh @ ${task.cost_basis:.2f})",
        results=[result],
        output_dir=output_dir,
    )
    _print_summary_tail(summary_path, [result])
    return 0 if result.rating != "FAILED" else 4


def _run_theme(task: Task, *, output_dir: Path, universe_df: pd.DataFrame) -> int:
    seed = f"Tickers that fit the theme '{task.theme}'."
    return _run_shortlist_flow(
        seed_prompt=seed,
        budget=task.budget,
        universe_df=universe_df,
        output_dir=output_dir,
        summary_prompt_label=f"theme: {task.theme}"
        + (f" (≤${task.budget})" if task.budget else ""),
    )


def _run_budget(task: Task, *, output_dir: Path, universe_df: pd.DataFrame) -> int:
    seed_parts = [f"Best value picks at or below ${task.budget} per share this week"]
    if task.theme:
        seed_parts.append(f"focused on {task.theme}")
    seed = ", ".join(seed_parts) + "."
    return _run_shortlist_flow(
        seed_prompt=seed,
        budget=task.budget,
        universe_df=universe_df,
        output_dir=output_dir,
        summary_prompt_label=f"budget: ≤${task.budget}"
        + (f" ({task.theme})" if task.theme else ""),
    )


def _run_compare(task: Task, *, output_dir: Path) -> int:
    from tradingagents.agent_assist.compare import run_compare
    return run_compare(
        task.ticker, task.ticker_b, output_dir=output_dir,
        deep_runner=_run_one_deep, summary_writer=write_summary,
    )


def _run_news_scan(task: Task, *, output_dir: Path) -> int:
    from tradingagents.agent_assist.news_scan import run_news_scan
    return run_news_scan(task.ticker, output_dir=output_dir)


def _run_freeform(task: Task, *, output_dir: Path, universe_df: pd.DataFrame) -> int:
    universe_set = set(universe_df["ticker"].astype(str).tolist())
    parsed: ParsedPrompt = parse_prompt(task.prompt, universe=universe_set)
    print(f"[parse] intent={parsed.intent} tickers={parsed.tickers} hold={parsed.hold_intent}")

    if parsed.intent == "single":
        # Route to the specific-ticker flow, optionally with a position prompt.
        from tradingagents.agent_assist.position import ask_position
        pos = ""
        if parsed.hold_intent:
            pos = ask_position(parsed.tickers[0]) or ""
            if pos:
                print(f"[position] {pos}")
        today = datetime.date.today().isoformat()
        result = _run_one_deep(parsed.tickers[0], today=today, position_str=pos)
        summary_path = write_summary(
            prompt=task.prompt, results=[result], output_dir=output_dir,
        )
        _print_summary_tail(summary_path, [result])
        return 0 if result.rating != "FAILED" else 4

    if parsed.intent == "multi":
        today = datetime.date.today().isoformat()
        results = [_run_one_deep(t, today=today) for t in parsed.tickers]
        summary_path = write_summary(
            prompt=task.prompt, results=results, output_dir=output_dir,
        )
        _print_summary_tail(summary_path, results)
        return 0

    # screen — same as theme but seed with the user's prompt verbatim
    used_budget = task.budget if task.budget is not None else _ask_budget()
    return _run_shortlist_flow(
        seed_prompt=task.prompt,
        budget=used_budget,
        universe_df=universe_df,
        output_dir=output_dir,
        summary_prompt_label=task.prompt,
    )


def _ask_budget() -> Optional[int]:
    raw = input("Max price per share? ($, Enter to skip): $").strip().lstrip("$")
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        print(f"Invalid budget '{raw}'; treating as no cap.")
        return None


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run_task(task: Task, *, output_dir: Optional[Path] = None) -> int:
    """Route a Task to the right flow and return a process exit code."""
    if output_dir is None:
        output_dir = Path.home() / ".tradingagents" / "agent_assist"

    universe_df = load_universe()

    if task.intent == "specific":
        return _run_specific(task, output_dir=output_dir)
    if task.intent == "owned":
        return _run_owned(task, output_dir=output_dir)
    if task.intent == "theme":
        return _run_theme(task, output_dir=output_dir, universe_df=universe_df)
    if task.intent == "budget":
        return _run_budget(task, output_dir=output_dir, universe_df=universe_df)
    if task.intent == "compare":
        return _run_compare(task, output_dir=output_dir)
    if task.intent == "news_scan":
        return _run_news_scan(task, output_dir=output_dir)
    if task.intent == "freeform":
        return _run_freeform(task, output_dir=output_dir, universe_df=universe_df)
    raise ValueError(f"unknown task intent: {task.intent!r}")


def main(prompt: str, budget: Optional[int] = None, output_dir: Optional[Path] = None) -> int:
    """Back-compat entry: build a freeform Task and delegate."""
    task = Task(intent="freeform", prompt=prompt, budget=budget)
    try:
        return run_task(task, output_dir=output_dir)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
