"""Stage-1 candidate discovery: prompt + universe + Ollama → 2-3 priced tickers.

The LLM picks names that match qualitative criteria (sector, theme); the
script enforces the quantitative price-vs-budget filter using yfinance.

For budget-driven flows the caller is expected to bulk-price the universe
(see ``bulk_price``) and hand the resulting dict back via
``precomputed_prices``. That lets the universe be pre-filtered to
sub-budget rows *before* the LLM sees it, so the LLM picks on fit, not
on guessed prices.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
from pydantic import BaseModel, Field

from tradingagents.agent_assist.config import (
    SHORTLIST_BASE_URL,
    SHORTLIST_MODEL,
    SHORTLIST_PROVIDER,
)
from tradingagents.llm_clients.factory import create_llm_client

logger = logging.getLogger(__name__)


DEFAULT_PRICE_CACHE_DIR = Path.home() / ".tradingagents" / "agent_assist_prices"

# yfinance recommends chunking large symbol lists; 200 is well under any limit.
_BULK_CHUNK = 200

# Day-scoped cache files older than this are purged on each bulk_price call.
# Keeps the cache dir from accumulating one stale file per day indefinitely.
_CACHE_RETENTION_DAYS = 7
_CACHE_PREFIX = "prices_"
_CACHE_EXT = ".json"


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


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


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


def _today_iso(today: Optional[_dt.date] = None) -> str:
    return (today or _dt.date.today()).isoformat()


def _cache_path(cache_dir: Path, today: Optional[_dt.date] = None) -> Path:
    return cache_dir / f"{_CACHE_PREFIX}{_today_iso(today)}{_CACHE_EXT}"


def _purge_stale_price_caches(
    cache_dir: Path,
    *,
    retention_days: int = _CACHE_RETENTION_DAYS,
    today: Optional[_dt.date] = None,
) -> int:
    """Delete ``prices_YYYY-MM-DD.json`` files older than ``retention_days``.

    Returns the count of files deleted. Failures (missing dir, permission
    denied on one file, malformed names) are logged at debug and otherwise
    swallowed — cache cleanup must never block the calling flow.
    """
    if not cache_dir.exists():
        return 0
    today = today or _dt.date.today()
    cutoff = today - _dt.timedelta(days=retention_days)
    deleted = 0
    for p in cache_dir.glob(f"{_CACHE_PREFIX}*{_CACHE_EXT}"):
        date_str = p.stem[len(_CACHE_PREFIX):]
        try:
            file_date = _dt.date.fromisoformat(date_str)
        except ValueError:
            logger.debug("skipping non-date price cache file: %s", p)
            continue
        if file_date < cutoff:
            try:
                p.unlink()
                deleted += 1
            except OSError as exc:
                logger.debug("could not delete stale cache %s: %s", p, exc)
    return deleted


def _read_price_cache(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): float(v) for k, v in data.items() if v is not None}
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("could not read price cache %s: %s", path, exc)
        return {}


def _write_price_cache(path: Path, prices: dict[str, float]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(prices, f)
        tmp.replace(path)
    except OSError as exc:
        logger.warning("could not write price cache %s: %s", path, exc)


def _yf_bulk_download(tickers: list[str]) -> dict[str, float]:
    """One yfinance multi-ticker download; returns last Close per ticker.

    Chunked internally because yfinance can struggle with very long
    symbol lists. Any ticker that does not come back with a valid Close
    is simply absent from the returned dict.
    """
    if not tickers:
        return {}
    try:
        import yfinance as yf
    except Exception as exc:  # pragma: no cover - import error is environmental
        logger.warning("yfinance import failed: %s", exc)
        return {}

    out: dict[str, float] = {}
    for i in range(0, len(tickers), _BULK_CHUNK):
        chunk = tickers[i : i + _BULK_CHUNK]
        try:
            data = yf.download(
                tickers=" ".join(chunk),
                period="1d",
                progress=False,
                group_by="ticker",
                threads=True,
                auto_adjust=False,
            )
        except Exception as exc:
            logger.warning("yfinance bulk download failed for chunk %d: %s", i, exc)
            continue

        if data is None or len(data) == 0:
            continue

        # Multi-ticker download → MultiIndex columns. Single ticker → flat columns.
        is_multi = isinstance(data.columns, pd.MultiIndex)
        for t in chunk:
            try:
                if is_multi:
                    if t not in data.columns.levels[0]:
                        continue
                    series = data[t].get("Close")
                else:
                    # Single-ticker chunk shouldn't happen here (we filter empties)
                    # but handle defensively.
                    series = data.get("Close")
                if series is None or len(series) == 0:
                    continue
                last = series.iloc[-1]
                if pd.notna(last):
                    out[t] = float(last)
            except (KeyError, IndexError, ValueError) as exc:
                logger.debug("skip %s in bulk download: %s", t, exc)
                continue
    return out


def bulk_price(
    tickers: Iterable[str],
    *,
    cache_dir: Optional[Path] = None,
    today: Optional[_dt.date] = None,
) -> dict[str, float]:
    """Return last-close prices for ``tickers``, cached for the local day.

    Strategy:
        1. Load today's cache from ``cache_dir`` if present.
        2. Identify tickers not yet cached.
        3. Bulk-download the missing set via yfinance in one call (chunked).
        4. Merge + persist the cache.
        5. Return prices for the tickers that were requested (cache hits + fresh).

    Tickers for which no price could be obtained are simply absent from
    the returned dict. Callers should treat missing keys as "no price".
    """
    cache_dir = cache_dir or DEFAULT_PRICE_CACHE_DIR
    _purge_stale_price_caches(cache_dir, today=today)
    path = _cache_path(cache_dir, today)
    tickers = list(dict.fromkeys(tickers))  # dedupe, preserve order

    cached = _read_price_cache(path)
    missing = [t for t in tickers if t not in cached]
    if missing:
        fresh = _yf_bulk_download(missing)
        if fresh:
            cached.update(fresh)
            _write_price_cache(path, cached)

    return {t: cached[t] for t in tickers if t in cached}


# ---------------------------------------------------------------------------
# LLM + prompt
# ---------------------------------------------------------------------------


def _build_llm():
    """Build the structured-output Ollama LLM for shortlisting."""
    client = create_llm_client(
        provider=SHORTLIST_PROVIDER,
        model=SHORTLIST_MODEL,
        base_url=SHORTLIST_BASE_URL,
    )
    llm = client.get_llm()
    return llm.with_structured_output(ShortList)


def _safe_cell(value) -> str:
    """Render a DataFrame cell value safely for the LLM prompt.

    ``pd.read_csv`` leaves missing values as ``NaN`` (a float); naive f-string
    interpolation produces the literal text ``nan``, which the LLM may then
    match on as if it were a real sector/industry name. Replace with an em-
    dash so the prompt clearly conveys "no value here".
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return str(value)


def _format_universe(
    universe: pd.DataFrame,
    prices: Optional[dict[str, float]] = None,
) -> str:
    """Compact text table the LLM can scan.

    Without prices: ``ticker | name | sector``.
    With prices:    ``ticker | name | sector | $price``.
    """
    if prices:
        rows = []
        for r in universe.itertuples(index=False):
            p = prices.get(r.ticker)
            price_col = f"${p:.2f}" if p is not None else "?"
            rows.append(
                f"{_safe_cell(r.ticker)} | {_safe_cell(r.name)} | "
                f"{_safe_cell(r.sector)} | {price_col}"
            )
        return "\n".join(rows)
    rows = (
        f"{_safe_cell(r.ticker)} | {_safe_cell(r.name)} | {_safe_cell(r.sector)}"
        for r in universe.itertuples(index=False)
    )
    return "\n".join(rows)


def _build_prompt(
    user_prompt: str,
    universe: pd.DataFrame,
    budget: Optional[int],
    exclude: Optional[list[str]] = None,
    prices: Optional[dict[str, float]] = None,
) -> str:
    if prices and budget is not None:
        budget_line = (
            f"\nAll names listed below are confirmed at or below the user's "
            f"**${budget}/share** budget — pick on fit, not on price."
        )
    elif budget is not None:
        budget_line = (
            f"\nThe user's budget is **${budget} per share** — only pick "
            f"tickers likely to be at or below this price."
        )
    else:
        budget_line = ""
    exclude_line = (
        f"\nThese were already considered and rejected — pick different ones: {', '.join(exclude)}."
        if exclude else ""
    )
    header = "Universe (ticker | company | sector | price):" if prices else "Universe (ticker | company | sector):"
    return f"""You are a stock screener. The user said:

  "{user_prompt}"
{budget_line}{exclude_line}

Pick 2-3 tickers from the universe below that best match. Use the ticker exactly as listed.
For each, give one sentence of reasoning that ties back to the user's prompt.

{header}
{_format_universe(universe, prices=prices)}
"""


# ---------------------------------------------------------------------------
# Top-level shortlist
# ---------------------------------------------------------------------------


def _resolve_price(
    ticker: str, precomputed: Optional[dict[str, float]]
) -> Optional[float]:
    """Lookup price from precomputed dict, or fall back to per-ticker fetch."""
    if precomputed is not None:
        return precomputed.get(ticker)
    return _price(ticker)


def _shortlist_round(
    llm,
    user_prompt: str,
    universe: pd.DataFrame,
    budget: Optional[int],
    exclude: list[str],
    *,
    precomputed_prices: Optional[dict[str, float]] = None,
) -> tuple[list[PricedCandidate], list[str]]:
    """Single LLM round: ask for picks, filter by price, return (kept, rejected).

    Shared by initial pass and reprompt — same logic for both rounds keeps
    the two-attempt contract honest: any survivor from either round
    deserves to reach the caller.
    """
    prompt = _build_prompt(
        user_prompt, universe, budget,
        exclude=exclude or None,
        prices=precomputed_prices,
    )
    result: ShortList = llm.invoke(prompt)

    kept: list[PricedCandidate] = []
    rejected: list[str] = []
    for cand in result.candidates:
        price = _resolve_price(cand.ticker, precomputed_prices)
        if price is None:
            rejected.append(cand.ticker)
            continue
        if budget is not None and price > budget:
            rejected.append(cand.ticker)
            continue
        kept.append(PricedCandidate(
            ticker=cand.ticker,
            reasoning=cand.reasoning,
            last_price=price,
        ))
    return kept, rejected


def shortlist(
    user_prompt: str,
    universe: pd.DataFrame,
    budget: Optional[int],
    *,
    precomputed_prices: Optional[dict[str, float]] = None,
) -> list[PricedCandidate]:
    """Run the shortlist stage; return surviving candidates with current prices.

    Args:
        user_prompt: The user's natural-language ask, verbatim.
        universe: DataFrame with at least columns ticker, name, sector.
        budget: Max per-share USD price, or None to skip the price filter.
        precomputed_prices: Optional dict of ``{ticker: last_price}`` for
            the rows in ``universe``. When provided, the LLM sees prices in
            its universe table, the per-ticker yfinance call is skipped,
            and the price filter is enforced against this dict only.

    Returns:
        List of PricedCandidate, possibly empty if nothing survives the filter
        even after one reprompt. May return 1 candidate when the reprompt
        also yields too few — better to show one valid pick than nothing.
    """
    llm = _build_llm()

    first, rejected = _shortlist_round(
        llm, user_prompt, universe, budget, exclude=[],
        precomputed_prices=precomputed_prices,
    )
    if len(first) >= 2:
        return first

    # Reprompt with the rejects flagged. Merge with whatever survived the
    # first round so a valid pick is never thrown away just because the
    # reprompt couldn't find a partner for it.
    second, _ = _shortlist_round(
        llm, user_prompt, universe, budget, exclude=rejected,
        precomputed_prices=precomputed_prices,
    )
    return _merge_dedup(first, second)


def _merge_dedup(a: list[PricedCandidate], b: list[PricedCandidate]) -> list[PricedCandidate]:
    seen = {c.ticker for c in a}
    out = list(a)
    for c in b:
        if c.ticker not in seen:
            out.append(c)
            seen.add(c.ticker)
    return out
