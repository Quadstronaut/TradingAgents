"""Stage-1 candidate discovery: prompt + universe + Ollama → 2-3 priced tickers.

The LLM picks names that match qualitative criteria (sector, theme); the
script enforces the quantitative price-vs-budget filter using yfinance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field

from tradingagents.agent_assist.config import (
    SHORTLIST_BASE_URL,
    SHORTLIST_MODEL,
    SHORTLIST_PROVIDER,
)
from tradingagents.llm_clients.factory import create_llm_client

logger = logging.getLogger(__name__)


class Candidate(BaseModel):
    ticker: str = Field(description="Ticker symbol from the supplied universe.")
    reasoning: str = Field(description="One sentence on why this ticker fits the user's prompt.")


class ShortList(BaseModel):
    candidates: list[Candidate] = Field(
        description="Two or three ticker candidates that best fit the user's prompt.",
        min_length=2,
        max_length=3,
    )


@dataclass(frozen=True)
class PricedCandidate:
    ticker: str
    reasoning: str
    last_price: float


def _build_llm():
    """Build the structured-output Ollama LLM for shortlisting."""
    client = create_llm_client(
        provider=SHORTLIST_PROVIDER,
        model=SHORTLIST_MODEL,
        base_url=SHORTLIST_BASE_URL,
    )
    llm = client.get_llm()
    return llm.with_structured_output(ShortList)


def _format_universe(universe: pd.DataFrame) -> str:
    """Compact text table the LLM can scan: ticker | name | sector."""
    rows = (
        f"{r.ticker} | {r.name} | {r.sector}"
        for r in universe.itertuples(index=False)
    )
    return "\n".join(rows)


def _build_prompt(user_prompt: str, universe: pd.DataFrame, budget: Optional[int],
                  exclude: Optional[list[str]] = None) -> str:
    budget_line = (
        f"\nThe user's budget is **${budget} per share** — only pick tickers likely to be at or below this price."
        if budget is not None else ""
    )
    exclude_line = (
        f"\nThese were already considered and rejected — pick different ones: {', '.join(exclude)}."
        if exclude else ""
    )
    return f"""You are a stock screener. The user said:

  "{user_prompt}"
{budget_line}{exclude_line}

Pick 2-3 tickers from the universe below that best match. Use the ticker exactly as listed.
For each, give one sentence of reasoning that ties back to the user's prompt.

Universe (ticker | company | sector):
{_format_universe(universe)}
"""


def _price(ticker: str) -> Optional[float]:
    """Return last price via yfinance.fast_info, or None on any failure.

    yfinance is imported lazily because its module-level init can be very
    slow (cookie/timezone setup hits the network). Keeping it out of the
    test import path lets unit tests patch this function directly.
    """
    try:
        import yfinance as yf
        return float(yf.Ticker(ticker).fast_info["lastPrice"])
    except Exception as exc:
        logger.warning("yfinance price failed for %s: %s", ticker, exc)
        return None


def shortlist(
    user_prompt: str,
    universe: pd.DataFrame,
    budget: Optional[int],
) -> list[PricedCandidate]:
    """Run the shortlist stage; return surviving candidates with current prices.

    Args:
        user_prompt: The user's natural-language ask, verbatim.
        universe: DataFrame with at least columns ticker, name, sector.
        budget: Max per-share USD price, or None to skip the price filter.

    Returns:
        List of PricedCandidate, possibly empty if nothing survives the filter
        even after one reprompt.
    """
    llm = _build_llm()

    rejected: list[str] = []
    for attempt in range(2):  # initial + one reprompt
        prompt = _build_prompt(user_prompt, universe, budget, exclude=rejected or None)
        result: ShortList = llm.invoke(prompt)

        priced: list[PricedCandidate] = []
        attempt_rejected: list[str] = []
        for cand in result.candidates:
            price = _price(cand.ticker)
            if price is None:
                attempt_rejected.append(cand.ticker)
                continue
            if budget is not None and price > budget:
                attempt_rejected.append(cand.ticker)
                continue
            priced.append(PricedCandidate(
                ticker=cand.ticker,
                reasoning=cand.reasoning,
                last_price=price,
            ))

        if len(priced) >= 2:
            return priced

        # First-pass survivors are too few — reprompt once with the rejects flagged.
        rejected.extend(attempt_rejected)
        if attempt == 0 and priced:
            # Carry over any single survivor from the first pass so we can pad with one more.
            survivors_to_keep = priced
            second = shortlist_round_two(llm, user_prompt, universe, budget, rejected)
            return _merge_dedup(survivors_to_keep, second)

    return []


def shortlist_round_two(llm, user_prompt, universe, budget, exclude) -> list[PricedCandidate]:
    """Helper for reprompt-only execution; same shape as shortlist() inner loop."""
    prompt = _build_prompt(user_prompt, universe, budget, exclude=exclude)
    result: ShortList = llm.invoke(prompt)
    out: list[PricedCandidate] = []
    for cand in result.candidates:
        price = _price(cand.ticker)
        if price is None:
            continue
        if budget is not None and price > budget:
            continue
        out.append(PricedCandidate(
            ticker=cand.ticker,
            reasoning=cand.reasoning,
            last_price=price,
        ))
    return out


def _merge_dedup(a: list[PricedCandidate], b: list[PricedCandidate]) -> list[PricedCandidate]:
    seen = {c.ticker for c in a}
    out = list(a)
    for c in b:
        if c.ticker not in seen:
            out.append(c)
            seen.add(c.ticker)
    return out
