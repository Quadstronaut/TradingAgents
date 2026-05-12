"""Interactive prompt for the user's current position in a ticker.

Returns a sentence the orchestrator threads into the Portfolio Manager
prompt via TradingAgentsGraph.propagate(..., additional_portfolio_context=...).

The rendered string matches the format produced by ``_run_owned`` in the
orchestrator, so the LLM sees a single canonical position-context shape
regardless of whether the position came from the menu or was inferred
from a freeform prompt.
"""

from __future__ import annotations

from typing import Optional


def _ask_non_negative_float(prompt: str) -> Optional[float]:
    """Prompt for a non-negative number; Enter returns None; reprompts on
    invalid or negative input.

    Strips a leading ``$`` so users can paste things like ``$127.45``.
    """
    while True:
        raw = input(prompt).strip().lstrip("$").strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            print(f"  '{raw}' is not a number.")
            continue
        if value < 0:
            print("  Must be zero or positive.")
            continue
        return value


def ask_position(ticker: str) -> Optional[str]:
    """Ask the user for share count and cost basis for ``ticker``.

    Returns a formatted sentence matching ``_run_owned``'s template, or
    None if the user skips either field (Enter on an empty prompt).
    Reprompts on non-numeric or negative input.
    """
    shares = _ask_non_negative_float(
        f"Do you currently hold {ticker}? How many shares (Enter to skip)? "
    )
    if shares is None:
        return None

    basis = _ask_non_negative_float(
        "Cost basis per share in USD (Enter to skip)? $"
    )
    if basis is None:
        return None

    return (
        f"User currently holds {shares:g} shares of {ticker} "
        f"at ${basis:.2f} cost basis."
    )
