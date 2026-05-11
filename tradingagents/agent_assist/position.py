"""Interactive prompt for the user's current position in a ticker.

Returns a sentence the orchestrator threads into the Portfolio Manager
prompt via TradingAgentsGraph.propagate(..., additional_portfolio_context=...).
"""

from __future__ import annotations

from typing import Optional


def ask_position(ticker: str) -> Optional[str]:
    """Ask the user for share count and cost basis for ``ticker``.

    Returns a formatted sentence, or None if the user skips either field.
    """
    raw_count = input(
        f"Do you currently hold {ticker}? "
        f"How many shares (Enter to skip)? "
    ).strip()
    if not raw_count:
        return None

    raw_basis = input(
        f"Cost basis per share in USD (Enter to skip)? $"
    ).strip().lstrip("$")
    if not raw_basis:
        return None

    return f"User currently holds {raw_count} shares of {ticker} at ${raw_basis} cost basis."
