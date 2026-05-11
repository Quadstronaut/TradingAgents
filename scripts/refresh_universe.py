"""Refresh tradingagents/agent_assist/data/ticker_universe.csv.

Fetches current S&P 500 + Nasdaq 100 constituents from Wikipedia and
writes a deduplicated CSV with columns: ticker, name, sector, industry.

Run manually (e.g. quarterly). Not invoked at runtime.

    uv run python scripts/refresh_universe.py
"""

import io
import sys
from pathlib import Path

import pandas as pd
import requests

OUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "tradingagents"
    / "agent_assist"
    / "data"
    / "ticker_universe.csv"
)

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

# Wikipedia 403s pandas/urllib's default User-Agent; spoof a browser to fetch.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TradingAgents-refresh/1.0; "
        "+https://github.com/tradingagents)"
    )
}


def _read_html(url: str) -> list:
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text))


def _fetch_sp500() -> pd.DataFrame:
    tables = _read_html(SP500_URL)
    df = None
    for t in tables:
        if "Symbol" in t.columns:
            df = t
            break
    if df is None:
        raise RuntimeError("Could not find S&P 500 constituents table on Wikipedia")
    return pd.DataFrame({
        "ticker": df["Symbol"].str.replace(".", "-", regex=False),  # BRK.B → BRK-B for yfinance
        "name": df["Security"],
        "sector": df["GICS Sector"],
        "industry": df["GICS Sub-Industry"],
    })


def _fetch_ndx() -> pd.DataFrame:
    tables = _read_html(NDX_URL)
    # Wikipedia layout shifts; pick the table that has a "Ticker" or "Symbol" column.
    target = None
    for t in tables:
        cols = [c.lower() for c in t.columns.astype(str)]
        if "ticker" in cols or "symbol" in cols:
            target = t
            break
    if target is None:
        raise RuntimeError("Could not find Nasdaq-100 constituents table on Wikipedia")

    sym_col = "Ticker" if "Ticker" in target.columns else "Symbol"
    for candidate in ("Company", "Security", "Name"):
        if candidate in target.columns:
            name_col = candidate
            break
    else:
        raise RuntimeError(
            f"Could not find a name column on Nasdaq-100 table. "
            f"Available columns: {list(target.columns)}"
        )
    sector_col = "GICS Sector" if "GICS Sector" in target.columns else None
    industry_col = "GICS Sub-Industry" if "GICS Sub-Industry" in target.columns else None

    return pd.DataFrame({
        "ticker": target[sym_col].astype(str).str.replace(".", "-", regex=False),
        "name": target[name_col].astype(str),
        "sector": target[sector_col] if sector_col else "",
        "industry": target[industry_col] if industry_col else "",
    })


def main() -> int:
    print(f"Fetching S&P 500 constituents from {SP500_URL}", file=sys.stderr)
    sp = _fetch_sp500()
    print(f"  → {len(sp)} rows", file=sys.stderr)

    print(f"Fetching Nasdaq-100 constituents from {NDX_URL}", file=sys.stderr)
    ndx = _fetch_ndx()
    print(f"  → {len(ndx)} rows", file=sys.stderr)

    combined = (
        pd.concat([sp, ndx], ignore_index=True)
        .drop_duplicates(subset=["ticker"], keep="first")
        .sort_values("ticker")
        .reset_index(drop=True)
    )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8")
    print(f"Wrote {len(combined)} rows to {OUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
