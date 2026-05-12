"""Position-context formatting + interactive collection.

Returns a sentence the orchestrator threads into the Portfolio Manager
prompt via TradingAgentsGraph.propagate(..., additional_portfolio_context=...).

Format is shared between the menu-driven "owned" task path and the
freeform path so the LLM sees a single canonical shape regardless of
how the position was collected.

When a current price is available, the helper also surfaces unrealized
P&L and an explicit behavioral-bias note for large drawdowns (>20%
unrealized loss) or large gains (>50% unrealized gain). This counters
the loss-aversion + disposition effects documented in Kahneman/Tversky
(prospect theory) and Lynch's "cutting flowers, watering weeds" rule —
the LLM should evaluate the thesis on current merits, not on the
user's anchoring to cost basis.
"""

from __future__ import annotations

from typing import Optional


# Threshold (% drawdown) at which loss-aversion bias becomes a documented
# decision drag. Below this we trust the user/LLM to weigh normally.
_LOSS_AVERSION_PCT = -20.0
# Threshold (% gain) at which the disposition effect becomes a documented
# drag — investors harvest winners too early.
_DISPOSITION_PCT = 50.0


def format_position_context(
    ticker: str,
    shares: float,
    cost_basis: float,
    *,
    current_price: Optional[float] = None,
) -> str:
    """Render the position-context sentence(s) for the Portfolio Manager.

    Always includes the holding ("holds N shares of T at $B cost basis").
    When ``current_price`` is supplied (and both prices are positive),
    appends unrealized P&L and — past the documented bias thresholds —
    one short behavioral-bias counter-prompt.
    """
    base = (
        f"User currently holds {shares:g} shares of {ticker} "
        f"at ${cost_basis:.2f} cost basis."
    )
    if current_price is None or current_price <= 0 or cost_basis <= 0:
        return base

    pnl_pct = (current_price - cost_basis) / cost_basis * 100.0
    sign = "+" if pnl_pct >= 0 else ""
    parts = [
        base,
        f"Current price ${current_price:.2f} ({sign}{pnl_pct:.1f}% vs cost).",
    ]
    if pnl_pct <= _LOSS_AVERSION_PCT:
        parts.append(
            "Note: position is at a large unrealized loss. Evaluate the "
            "thesis on current merits — loss-aversion bias often drags "
            "investors into holding broken theses to avoid realising the "
            "loss. Don't anchor on cost basis."
        )
    elif pnl_pct >= _DISPOSITION_PCT:
        parts.append(
            "Note: position is well above cost. The disposition effect "
            "often pushes investors to harvest winners prematurely. "
            "Evaluate whether the thesis still holds at the current price, "
            "not whether the gain feels 'enough'."
        )
    return " ".join(parts)


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


def ask_position(
    ticker: str, *, current_price: Optional[float] = None,
) -> Optional[str]:
    """Ask the user for share count and cost basis for ``ticker``.

    Returns a formatted sentence using :func:`format_position_context`, or
    ``None`` if the user skips either field (Enter on an empty prompt).
    Reprompts on non-numeric or negative input. Pass ``current_price`` to
    enrich the output with unrealized P&L + bias counter-prompts.
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

    return format_position_context(
        ticker, shares, basis, current_price=current_price,
    )
