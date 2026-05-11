"""Orchestrator: parse prompt → optional shortlist → per-ticker confirm → propagate → summarize."""

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
from tradingagents.agent_assist.position import ask_position
from tradingagents.agent_assist.prompt_parse import ParsedPrompt, parse_prompt
from tradingagents.agent_assist.shortlist import PricedCandidate, shortlist
from tradingagents.agent_assist.summarize import RunResult, write_summary
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

logger = logging.getLogger(__name__)


# Match "**BUY**" / "**Buy**" etc. inside the TradingAgents final markdown.
_RATING_RE = re.compile(
    r"FINAL TRANSACTION PROPOSAL:\s*\*\*\s*(Buy|Overweight|Hold|Underweight|Sell)\s*\*\*",
    re.IGNORECASE,
)
_VALID_RATINGS = {"Buy", "Overweight", "Hold", "Underweight", "Sell"}


def load_universe() -> pd.DataFrame:
    """Load the bundled ticker universe CSV."""
    path = files("tradingagents.agent_assist.data").joinpath("ticker_universe.csv")
    with path.open("r", encoding="utf-8") as f:
        return pd.read_csv(f)


def _ask_budget() -> Optional[int]:
    raw = input("Max price per share? ($, Enter to skip): $").strip().lstrip("$")
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        print(f"Invalid budget '{raw}'; treating as no cap.")
        return None


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


def _resolve_tickers(parsed: ParsedPrompt, prompt: str, budget: Optional[int],
                     universe: pd.DataFrame) -> tuple[list[PricedCandidate], Optional[int]]:
    """Decide between explicit tickers and the shortlist stage; return (candidates, used_budget)."""
    if parsed.intent in ("single", "multi"):
        cands = [
            PricedCandidate(ticker=t, reasoning="(explicitly named in prompt)", last_price=0.0)
            for t in parsed.tickers
        ]
        return cands, budget

    used_budget = budget if budget is not None else _ask_budget()
    cands = shortlist(prompt, universe, budget=used_budget)
    return cands, used_budget


def main(prompt: str, budget: Optional[int] = None, output_dir: Optional[Path] = None) -> int:
    """Orchestrator entry. Returns process exit code."""
    if output_dir is None:
        output_dir = Path.home() / ".tradingagents" / "agent_assist"

    universe_df = load_universe()
    universe_set = set(universe_df["ticker"].astype(str).tolist())

    parsed = parse_prompt(prompt, universe=universe_set)
    print(f"[parse] intent={parsed.intent} tickers={parsed.tickers} hold={parsed.hold_intent}")

    candidates, _ = _resolve_tickers(parsed, prompt, budget, universe_df)

    if not candidates:
        print("No candidates to analyze. Try a more specific prompt.")
        return 3

    print()
    print(f"=== Shortlist ({len(candidates)} candidates) ===")
    for c in candidates:
        price_str = f" @ ${c.last_price:.2f}" if c.last_price else ""
        print(f"  - {c.ticker}{price_str} - {c.reasoning}")
    est_min = len(candidates) * 15
    print(f"Estimated time if you run all: ~{est_min} min.")
    print()

    # Position context: only when sell/hold-flavored OR exactly one ticker.
    position_str = ""
    if parsed.hold_intent or parsed.intent == "single":
        target = candidates[0].ticker
        result = ask_position(target)
        if result:
            position_str = result
            print(f"[position] {position_str}")

    results: list[RunResult] = []
    config = _build_config()
    today = datetime.date.today().isoformat()

    for c in candidates:
        choice = _confirm_run(c.ticker)
        if choice == "s":
            results.append(RunResult(ticker=c.ticker, rating="SKIPPED", log_path=None, error=None))
            continue
        if choice == "a":
            print(f"Aborting before {c.ticker}.")
            break

        try:
            ta = TradingAgentsGraph(debug=False, config=config)
            _, decision = ta.propagate(
                c.ticker,
                today,
                additional_portfolio_context=position_str if c.ticker == candidates[0].ticker else "",
            )
            rating = _extract_rating(decision)
            log_dir = Path(config["results_dir"]) / c.ticker
            results.append(RunResult(ticker=c.ticker, rating=rating, log_path=log_dir, error=None))
            print(f"[done] {c.ticker} -> {rating}")
        except Exception as exc:
            logger.exception("propagate failed for %s", c.ticker)
            results.append(RunResult(ticker=c.ticker, rating="FAILED", log_path=None, error=str(exc)))

    summary_path = write_summary(prompt=prompt, results=results, output_dir=output_dir)
    print()
    print(f"=== Summary written: {summary_path} ===")
    for r in results:
        print(f"  {r.ticker:<8} {r.rating}")

    return 0
