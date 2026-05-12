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

import dataclasses
import datetime
import logging
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
from tradingagents.agent_assist.prompt_parse import (
    ParsedPrompt,
    normalize_ticker,
    parse_prompt,
)
from tradingagents.agent_assist.shortlist import (
    PricedCandidate,
    bulk_price,
    shortlist,
    trading_days_until_earnings,
)
from tradingagents.agent_assist.summarize import RunResult, write_summary
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

logger = logging.getLogger(__name__)


# Per-deep-run estimate used to populate the progress "est. remaining" hint.
DEEP_RUN_SECONDS = 15 * 60


def load_universe() -> pd.DataFrame:
    """Load the bundled ticker universe CSV."""
    path = files("tradingagents.agent_assist.data").joinpath("ticker_universe.csv")
    with path.open("r", encoding="utf-8") as f:
        return pd.read_csv(f)


_EARNINGS_BLACKOUT_DAYS = 5


def _maybe_print_earnings_warning(ticker: str, today: Optional[datetime.date] = None) -> None:
    """Surface an earnings-imminent warning before a deep run starts.

    Earnings releases create 5-10% daily moves and an Earnings Announcement
    Premium (NBER w13090); a 15-minute analysis kicked off ≤5 trading days
    before earnings is likely to be invalidated by tomorrow's release.
    Practitioner convention is a ±3-5 day blackout. We don't block — the
    decision is contextual (some users analyze *because* of earnings) — but
    the user should know before committing the wall-clock cost.
    """
    days = trading_days_until_earnings(ticker, today=today)
    if days is None:
        return
    if 0 <= days <= _EARNINGS_BLACKOUT_DAYS:
        when = "today" if days == 0 else f"in ~{days} trading day(s)"
        print(
            f"[earnings warning] {ticker} reports {when}. Deep analysis may "
            f"be invalidated by the release."
        )


def _confirm_run(ticker: str) -> str:
    """Returns 'y', 's', or 'a'."""
    _maybe_print_earnings_warning(ticker)
    while True:
        raw = input(f"Run deep analysis on {ticker} (~15 min)? [y]es / [s]kip / [a]bort: ").strip().lower()
        if raw in ("y", "yes"):
            return "y"
        if raw in ("s", "skip"):
            return "s"
        if raw in ("a", "abort"):
            return "a"
        print("Please answer y, s, or a.")


def _confirm_bulk(n: int) -> bool:
    """Single-prompt confirm before kicking off N back-to-back deep runs.

    Used by multi-ticker freeform paths where the user already named the
    tickers — per-ticker confirm would be noise; one gate is enough."""
    while True:
        raw = input(f"Run all {n}? [y/N]: ").strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("", "n", "no"):
            return False
        print("Please answer y or n.")


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
            # propagate returns (final_state, rating). The rating is the
            # bare canonical word (Buy/Overweight/Hold/Underweight/Sell)
            # already extracted by SignalProcessor from the PM's markdown;
            # the markdown itself lives at final_state["final_trade_decision"].
            final_state, rating = ta.propagate(
                ticker,
                today,
                additional_portfolio_context=position_str,
            )
            ps.finish()
        decision_md = final_state.get("final_trade_decision", "")
        log_dir = Path(config["results_dir"]) / ticker
        print(f"[done] {ticker} -> {rating}")
        return RunResult(
            ticker=ticker, rating=rating, log_path=log_dir, error=None,
            decision_md=decision_md,
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
    summary_prompt_label: Optional[str] = None,
    precomputed_prices: Optional[dict[str, float]] = None,
    empty_diagnostic: Optional[str] = None,
) -> int:
    """Shared multi-ticker flow: shortlist → confirm each → deep run → summary."""
    candidates = shortlist(
        seed_prompt, universe_df, budget=budget,
        precomputed_prices=precomputed_prices,
    )
    if not candidates:
        print(empty_diagnostic or "No candidates to analyze. Try a more specific prompt.")
        return 3

    _print_shortlist(candidates)

    results: list[RunResult] = []
    config = _build_config()

    for c in candidates:
        choice = _confirm_run(c.ticker)
        if choice == "s":
            results.append(RunResult(ticker=c.ticker, rating="SKIPPED", log_path=None, error=None))
            continue
        if choice == "a":
            print(f"Aborting before {c.ticker}.")
            break

        # Recompute today per ticker — a 3-ticker run that starts at 23:55
        # would otherwise stamp tickers 2 and 3 with yesterday's date and
        # miss today's news.
        today = datetime.date.today().isoformat()
        result = _run_one_deep(c.ticker, today=today, config=config)
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
    _maybe_print_earnings_warning(task.ticker)
    result = _run_one_deep(task.ticker, today=today)
    summary_path = write_summary(
        prompt=f"specific: {task.ticker}",
        results=[result],
        output_dir=output_dir,
    )
    _print_summary_tail(summary_path, [result])
    return 0 if result.rating != "FAILED" else 4


def _run_owned(task: Task, *, output_dir: Path) -> int:
    if task.shares is None or task.cost_basis is None:
        raise ValueError(
            "owned intent requires both shares and cost_basis "
            f"(got shares={task.shares!r}, cost_basis={task.cost_basis!r})"
        )
    today = datetime.date.today().isoformat()
    pos = (
        f"User currently holds {task.shares:g} shares of {task.ticker} "
        f"at ${task.cost_basis:.2f} cost basis."
    )
    print(f"[position] {pos}")
    _maybe_print_earnings_warning(task.ticker)
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


def _theme_filter(universe_df: pd.DataFrame, theme: str) -> pd.DataFrame:
    """Case-insensitive substring match across sector, industry, name, ticker."""
    t = theme.strip().lower()
    if not t:
        return universe_df
    cols = [c for c in ("sector", "industry", "name", "ticker") if c in universe_df.columns]
    mask = pd.Series(False, index=universe_df.index)
    for c in cols:
        mask = mask | universe_df[c].fillna("").astype(str).str.lower().str.contains(t, regex=False)
    return universe_df[mask]


# Minimum sub-budget rows after the theme filter before we trust it; below
# this we drop the theme filter and let the LLM soft-match on the wider set.
_THEME_HARD_MIN = 5


def _run_budget(task: Task, *, output_dir: Path, universe_df: pd.DataFrame) -> int:
    """Budget flow: pre-price + pre-filter universe so the LLM sees only affordables.

    Steps:
        1. Bulk-price the whole universe (day-cached).
        2. Filter to ``price <= budget``.
        3. If a theme was given, try hard substring matching across
           sector/industry/name/ticker. If <``_THEME_HARD_MIN`` rows survive,
           drop the theme filter and pass the wider set to the LLM (soft match).
        4. If still <2 rows, print a specific diagnostic and exit cleanly.
        5. Hand the filtered, priced subset to the shortlist stage.
    """
    print(f"[price] fetching current prices for {len(universe_df)} names...")
    all_prices = bulk_price(universe_df["ticker"].astype(str).tolist())
    if not all_prices:
        print(
            "Could not fetch prices (yfinance unreachable?). Falling back to "
            "the LLM-only path — results may be off-budget."
        )
        seed_parts = [f"Best value picks at or below ${task.budget} per share this week"]
        if task.theme:
            seed_parts.append(f"focused on {task.theme}")
        seed = ", ".join(seed_parts) + "."
        return _run_shortlist_flow(
            seed_prompt=seed,
            budget=task.budget,
            universe_df=universe_df,
            output_dir=output_dir,
            summary_prompt_label=f"budget: <=${task.budget}"
            + (f" ({task.theme})" if task.theme else ""),
        )

    priced_df = universe_df[universe_df["ticker"].isin(all_prices.keys())].copy()
    affordable_df = priced_df[
        priced_df["ticker"].map(all_prices) <= task.budget
    ].copy()

    n_total = len(priced_df)
    n_affordable = len(affordable_df)
    print(f"[price] {n_affordable} of {n_total} names trade at or below ${task.budget}.")

    if n_affordable < 2:
        print(
            f"Only {n_affordable} of {n_total} names trade at or below ${task.budget}. "
            f"Try a higher cap (e.g. ${max(task.budget * 2, 50)})."
        )
        return 3

    # Theme handling: hard filter, soft-fallback if too sparse.
    used_filter = affordable_df
    theme_note = ""
    if task.theme:
        hard = _theme_filter(affordable_df, task.theme)
        if len(hard) >= _THEME_HARD_MIN:
            used_filter = hard
            theme_note = (
                f"[theme] {len(hard)} names match '{task.theme}' "
                f"(sector/industry/name substring)."
            )
        else:
            theme_note = (
                f"[theme] only {len(hard)} hard matches for '{task.theme}'; "
                f"letting the LLM soft-match across all {n_affordable} affordables."
            )
        print(theme_note)

    if len(used_filter) < 2:
        print(
            f"After theme filter only {len(used_filter)} affordable names remain. "
            f"Try a different theme or drop it."
        )
        return 3

    # Seed phrasing changes once the universe is pre-filtered: the LLM no
    # longer has to think about price, just about fit.
    seed_parts = [f"Best picks under ${task.budget}/share this week"]
    if task.theme:
        seed_parts.append(f"matching the theme '{task.theme}'")
    seed = ", ".join(seed_parts) + "."

    label = f"budget: <=${task.budget}" + (f" ({task.theme})" if task.theme else "")
    return _run_shortlist_flow(
        seed_prompt=seed,
        budget=task.budget,
        universe_df=used_filter,
        output_dir=output_dir,
        summary_prompt_label=label,
        precomputed_prices={t: all_prices[t] for t in used_filter["ticker"]},
        empty_diagnostic=(
            f"The LLM could not pick from the {len(used_filter)} sub-${task.budget} candidates. "
            f"Try a different theme or a higher cap."
        ),
    )


def _run_compare(task: Task, *, output_dir: Path) -> int:
    from tradingagents.agent_assist.compare import run_compare
    _maybe_print_earnings_warning(task.ticker)
    _maybe_print_earnings_warning(task.ticker_b)
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
        _maybe_print_earnings_warning(parsed.tickers[0])
        result = _run_one_deep(parsed.tickers[0], today=today, position_str=pos)
        summary_path = write_summary(
            prompt=task.prompt, results=[result], output_dir=output_dir,
        )
        _print_summary_tail(summary_path, [result])
        return 0 if result.rating != "FAILED" else 4

    if parsed.intent == "multi":
        n = len(parsed.tickers)
        est_min = n * 15
        print(
            f"Found {n} tickers: {', '.join(parsed.tickers)}. "
            f"Estimated time: ~{est_min} min."
        )
        if not _confirm_bulk(n):
            print("Aborted.")
            return 0
        # Recompute today per ticker so a multi-run spanning midnight stamps
        # each ticker with its actual start date.
        results = []
        for t in parsed.tickers:
            _maybe_print_earnings_warning(t)
            results.append(_run_one_deep(t, today=datetime.date.today().isoformat()))
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
    """Route a Task to the right flow and return a process exit code.

    Normalises ``task.ticker`` / ``task.ticker_b`` once here so every
    downstream flow (yfinance fetchers, path joins, position strings,
    LLM prompts) sees the canonical spelling regardless of how the Task
    was constructed (menu, freeform parse, or programmatic test).
    """
    if output_dir is None:
        output_dir = Path.home() / ".tradingagents" / "agent_assist"

    if task.ticker:
        task = dataclasses.replace(task, ticker=normalize_ticker(task.ticker))
    if task.ticker_b:
        task = dataclasses.replace(task, ticker_b=normalize_ticker(task.ticker_b))

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
