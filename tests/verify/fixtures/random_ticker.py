"""Seeded ticker picker over the bundled universe.

A pass number maps to a deterministic ticker (or set of tickers). Three
consecutive passes hit three different names by advancing the rotor.

Loading the universe is cached on first call; tests should not need to
mock the CSV read for performance.
"""

from __future__ import annotations

import functools
from importlib.resources import files
from pathlib import Path
from typing import Optional

import pandas as pd

# Step is co-prime with most realistic universe sizes (568 today). That
# means the rotor visits every row before any repeats, instead of
# collapsing onto a short cycle.
_DEFAULT_STEP = 17

# Exclusion list (one ticker per line, '#' comments allowed). Populated by
# a one-shot scan during harness build for known-bad yfinance fetches.
_EXCLUSIONS_FILE = "ticker_exclusions.txt"


@functools.lru_cache(maxsize=1)
def load_universe() -> pd.DataFrame:
    """Read the bundled ``ticker_universe.csv`` and return it as a DataFrame."""
    path = files("tradingagents.agent_assist.data").joinpath("ticker_universe.csv")
    with path.open("r", encoding="utf-8") as f:
        return pd.read_csv(f)


@functools.lru_cache(maxsize=1)
def _load_exclusions() -> frozenset[str]:
    path = Path(__file__).with_name(_EXCLUSIONS_FILE)
    if not path.exists():
        return frozenset()
    out: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.add(line.upper())
    return frozenset(out)


def _eligible_tickers() -> list[str]:
    df = load_universe()
    excl = _load_exclusions()
    return [t for t in df["ticker"].astype(str).tolist() if t.upper() not in excl]


def pick_ticker(pass_no: int, *, step: int = _DEFAULT_STEP) -> str:
    """Return a deterministic ticker for ``pass_no``.

    Passes 0, 1, 2 hit three distinct names; later passes continue to
    rotate through the universe before repeating.
    """
    if pass_no < 0:
        raise ValueError(f"pass_no must be non-negative; got {pass_no!r}")
    tickers = _eligible_tickers()
    if not tickers:
        raise RuntimeError(
            "No eligible tickers after exclusions — check ticker_universe.csv "
            "and tests/verify/fixtures/ticker_exclusions.txt."
        )
    idx = (pass_no * step) % len(tickers)
    return tickers[idx]


def pick_tickers(pass_no: int, n: int, *, step: int = _DEFAULT_STEP) -> list[str]:
    """Return ``n`` distinct tickers for ``pass_no`` (used by compare flow)."""
    if n <= 0:
        raise ValueError(f"n must be positive; got {n!r}")
    tickers = _eligible_tickers()
    if n > len(tickers):
        raise ValueError(f"requested {n} tickers; only {len(tickers)} eligible")
    out: list[str] = []
    seen: set[str] = set()
    cursor = pass_no * step
    while len(out) < n:
        candidate = tickers[cursor % len(tickers)]
        if candidate not in seen:
            out.append(candidate)
            seen.add(candidate)
        cursor += 1
    return out


def add_exclusion(ticker: str, reason: Optional[str] = None) -> None:
    """Append ``ticker`` to the exclusions file. Idempotent.

    Called by the harness's one-shot scan and by Stage 3 when a fixture
    is observed to be unfetchable. Bust the cache so subsequent picks
    see the new exclusion within the same process.
    """
    path = Path(__file__).with_name(_EXCLUSIONS_FILE)
    existing = _load_exclusions()
    if ticker.upper() in existing:
        return
    line = ticker.upper()
    if reason:
        line = f"{line:<10} # {reason}"
    line += "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
    _load_exclusions.cache_clear()
