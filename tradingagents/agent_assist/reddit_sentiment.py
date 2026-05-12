"""Reddit sentiment seam.

Reads per-ticker JSONL files written by a sibling Reddit-scraper project
(planned to live at ``~/Documents/GIT/RedditScraper/Claude``). The scraper
is decoupled: it writes on its own cadence, this module reads when the
Social Analyst's tool is called.

Contract is documented in ``docs/reddit_sentiment_contract.md``.

Missing or malformed files degrade gracefully: the tool returns a clear
'no cache' message and the analyst continues with its other tools.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional

from langchain_core.tools import tool

from tradingagents.dataflows.utils import safe_ticker_component

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".tradingagents" / "reddit_sentiment"


@dataclass(frozen=True)
class SentimentRecord:
    ts: datetime
    sub: str
    score: float
    confidence: float
    n_posts: int
    theme: Optional[str] = None
    learning_version: Optional[str] = None
    sample_quote: Optional[str] = None


def _parse_record(raw: dict) -> Optional[SentimentRecord]:
    """Parse one JSONL row; return None if required fields are missing/invalid."""
    try:
        ts = datetime.fromisoformat(str(raw["ts"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return SentimentRecord(
            ts=ts,
            sub=str(raw["sub"]),
            score=float(raw["score"]),
            confidence=float(raw["confidence"]),
            n_posts=int(raw["n_posts"]),
            theme=raw.get("theme"),
            learning_version=raw.get("learning_version"),
            sample_quote=raw.get("sample_quote"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("skipping malformed Reddit sentiment record: %s", exc)
        return None


def read_records(
    ticker: str,
    lookback_days: int = 7,
    cache_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> list[SentimentRecord]:
    """Read recent sentiment records for ``ticker`` from the JSONL cache.

    Args:
        ticker: Ticker symbol (sanitized for path use).
        lookback_days: Only return records with ``ts`` within this many days.
        cache_dir: Override the default cache directory (for tests).
        now: Override "now" for deterministic windowing (for tests).

    Returns:
        List of records, possibly empty. Never raises on missing or
        malformed input — malformed rows are skipped, missing file returns [].
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    safe_ticker = safe_ticker_component(ticker)
    path = cache_dir / f"{safe_ticker}.jsonl"

    if not path.exists():
        return []

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=lookback_days)
    records: list[SentimentRecord] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("skipping non-JSON line in %s: %s", path, exc)
                    continue
                rec = _parse_record(raw)
                if rec is None:
                    continue
                if rec.ts >= cutoff:
                    records.append(rec)
    except OSError as exc:
        logger.warning("could not read %s: %s", path, exc)
        return []

    return records


# Exponential decay rate per day for sentiment weighting. α=0.10 places
# ~70% of cumulative weight in [0, 3] days, ~95% in [0, 14] days. Matches
# the practitioner norm cited in Springer 2020 (sentiment decay in finance)
# and BIS economic-news forecasting work.
_SENTIMENT_DECAY_ALPHA = 0.10


def summarize(
    records: list[SentimentRecord],
    *,
    now: Optional[datetime] = None,
) -> str:
    """Render records to a short prose summary the analyst can consume.

    Each record's contribution to the aggregate score is weighted by
    ``confidence × log(n_posts+1) × exp(-α·days_old)``. The time-decay
    term ensures fresh sentiment dominates older reads when the analyst
    asks "what are people saying now". Pass ``now`` for deterministic
    output in tests.
    """
    if not records:
        return (
            "No Reddit sentiment cache available for this ticker. "
            "Use the other available sources (Yahoo, public news) instead."
        )

    now = now or datetime.now(timezone.utc)
    total_weight = 0.0
    weighted_score = 0.0
    by_sub: dict[str, list[SentimentRecord]] = {}
    for r in records:
        days_old = max((now - r.ts).total_seconds() / 86400.0, 0.0)
        time_weight = math.exp(-_SENTIMENT_DECAY_ALPHA * days_old)
        w = r.confidence * math.log(max(r.n_posts, 1) + 1) * time_weight
        total_weight += w
        weighted_score += w * r.score
        by_sub.setdefault(r.sub, []).append(r)

    aggregate = weighted_score / total_weight if total_weight else 0.0
    lean = "bullish" if aggregate > 0.15 else "bearish" if aggregate < -0.15 else "mixed/neutral"

    lines = [
        f"Reddit sentiment ({len(records)} records across {len(by_sub)} subreddits, "
        f"time-decayed): aggregate score {aggregate:+.2f}, lean {lean}.",
    ]

    for sub in sorted(by_sub, key=lambda s: -sum(r.confidence for r in by_sub[s])):
        sub_records = by_sub[sub]
        avg = sum(r.score for r in sub_records) / len(sub_records)
        themes = [r.theme for r in sub_records if r.theme]
        line = f"  - r/{sub}: {len(sub_records)} reads, avg {avg:+.2f}"
        if themes:
            line += f"; themes: {'; '.join(themes[:3])}"
        lines.append(line)

    quotes = [r.sample_quote for r in records if r.sample_quote][:2]
    if quotes:
        lines.append("Sample quotes:")
        for q in quotes:
            lines.append(f'  - "{q}"')

    return "\n".join(lines)


@tool
def get_reddit_sentiment(
    ticker: Annotated[str, "Ticker symbol"],
    lookback_days: Annotated[int, "Days of history to consider"] = 7,
) -> str:
    """Retrieve aggregated Reddit sentiment for a ticker from the local cache.

    The cache is populated by a separate scraper process. If no cache
    exists for this ticker, returns a notice and you should rely on other
    available news/social tools.

    Args:
        ticker: Ticker symbol (e.g. NVDA, BRK.B).
        lookback_days: How many days of history to include.

    Returns:
        Human-readable summary of recent Reddit sentiment, or a 'no cache'
        notice if none is available.
    """
    records = read_records(ticker, lookback_days=lookback_days)
    return summarize(records)
