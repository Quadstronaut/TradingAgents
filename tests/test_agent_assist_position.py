"""Unit tests for position-context prompting. input() is mocked.

The rendered string must match the format produced by
``orchestrator._run_owned`` so the LLM sees a single canonical
position-context shape regardless of how the position was collected.

When a current price is supplied, the helper also surfaces unrealized
P&L plus a behavioural-bias counter-prompt at large drawdowns / gains.
"""

from unittest.mock import patch

import pytest

from tradingagents.agent_assist.position import (
    ask_position,
    format_position_context,
)


@pytest.mark.unit
def test_returns_formatted_string_when_both_inputs_given():
    with patch("builtins.input", side_effect=["50", "130"]):
        result = ask_position("NVDA")
    assert result == "User currently holds 50 shares of NVDA at $130.00 cost basis."


@pytest.mark.unit
def test_accepts_decimal_cost_basis():
    with patch("builtins.input", side_effect=["10", "127.45"]):
        result = ask_position("AAPL")
    assert result == "User currently holds 10 shares of AAPL at $127.45 cost basis."


@pytest.mark.unit
def test_accepts_fractional_shares():
    """``_ask_float`` in the menu's owned flow allows fractional shares;
    ask_position must allow them too (some brokers fractional)."""
    with patch("builtins.input", side_effect=["1.5", "100"]):
        result = ask_position("AAPL")
    assert result == "User currently holds 1.5 shares of AAPL at $100.00 cost basis."


@pytest.mark.unit
def test_returns_none_when_share_count_blank():
    with patch("builtins.input", side_effect=[""]):
        result = ask_position("NVDA")
    assert result is None


@pytest.mark.unit
def test_returns_none_when_cost_basis_blank():
    with patch("builtins.input", side_effect=["50", ""]):
        result = ask_position("NVDA")
    assert result is None


@pytest.mark.unit
def test_strips_dollar_sign_from_cost_basis():
    with patch("builtins.input", side_effect=["50", "$130"]):
        result = ask_position("NVDA")
    assert result == "User currently holds 50 shares of NVDA at $130.00 cost basis."


@pytest.mark.unit
def test_reprompts_on_non_numeric_share_count(capsys):
    """User typo on share count → reprompt, then accept."""
    with patch("builtins.input", side_effect=["abc", "50", "130"]):
        result = ask_position("NVDA")
    assert result == "User currently holds 50 shares of NVDA at $130.00 cost basis."
    out = capsys.readouterr().out
    assert "'abc' is not a number" in out


@pytest.mark.unit
def test_reprompts_on_non_numeric_cost_basis(capsys):
    """Same for cost basis."""
    with patch("builtins.input", side_effect=["50", "many dollars", "130"]):
        result = ask_position("NVDA")
    assert result == "User currently holds 50 shares of NVDA at $130.00 cost basis."
    out = capsys.readouterr().out
    assert "is not a number" in out


@pytest.mark.unit
def test_reprompts_on_negative_share_count(capsys):
    """Negative shares are meaningless — reprompt."""
    with patch("builtins.input", side_effect=["-5", "50", "130"]):
        result = ask_position("NVDA")
    assert result == "User currently holds 50 shares of NVDA at $130.00 cost basis."
    out = capsys.readouterr().out
    assert "zero or positive" in out


@pytest.mark.unit
def test_reprompts_on_negative_cost_basis():
    with patch("builtins.input", side_effect=["50", "-10", "130"]):
        result = ask_position("NVDA")
    assert result == "User currently holds 50 shares of NVDA at $130.00 cost basis."


@pytest.mark.unit
def test_format_matches_run_owned_template():
    """Both paths must produce identical position-context strings — assert
    the freeform/ask_position output equals what _run_owned would build
    from the same shares + basis values (with no current price provided,
    no P&L tail)."""
    shares = 100.0
    basis = 142.75
    ticker = "AMD"
    with patch("builtins.input", side_effect=[str(shares), str(basis)]):
        ask_position_output = ask_position(ticker)

    expected = format_position_context(ticker, shares, basis)
    assert ask_position_output == expected
    # And asserts the shape hasn't drifted.
    assert ask_position_output == (
        f"User currently holds {shares:g} shares of {ticker} "
        f"at ${basis:.2f} cost basis."
    )


# ---------------------------------------------------------------------------
# format_position_context: P&L + behavioural-bias surfacing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_format_includes_pnl_when_current_price_known():
    out = format_position_context("NVDA", 50, 100.0, current_price=130.0)
    assert "Current price $130.00" in out
    assert "+30.0% vs cost" in out


@pytest.mark.unit
def test_format_negative_pnl_formats_correctly():
    out = format_position_context("NVDA", 50, 100.0, current_price=85.0)
    assert "Current price $85.00" in out
    assert "-15.0% vs cost" in out
    # Loss below the loss-aversion threshold (-20%) → no bias counter-prompt yet.
    assert "loss-aversion" not in out.lower()


@pytest.mark.unit
def test_format_flags_loss_aversion_past_20pct_drawdown():
    """≤-20% unrealized → prompt the LLM to evaluate thesis on merits, not
    on user's anchoring to cost basis. Counters documented bias."""
    out = format_position_context("NVDA", 50, 100.0, current_price=70.0)
    assert "-30.0% vs cost" in out
    assert "loss-aversion" in out.lower()
    assert "anchor on cost basis" in out.lower()


@pytest.mark.unit
def test_format_flags_loss_aversion_at_exact_threshold():
    """-20% exactly should trip the flag (the threshold is documented at
    -20%, not "below -20%")."""
    out = format_position_context("NVDA", 50, 100.0, current_price=80.0)
    assert "-20.0% vs cost" in out
    assert "loss-aversion" in out.lower()


@pytest.mark.unit
def test_format_flags_disposition_effect_past_50pct_gain():
    """≥+50% unrealized → counter the disposition effect (harvesting
    winners too early). Lynch / behavioural finance."""
    out = format_position_context("NVDA", 50, 100.0, current_price=160.0)
    assert "+60.0% vs cost" in out
    assert "disposition effect" in out.lower()


@pytest.mark.unit
def test_format_no_pnl_tail_when_current_price_missing():
    """current_price=None or non-positive → fall back to the bare position
    sentence, no fabricated P&L."""
    for cp in (None, 0.0, -5.0):
        out = format_position_context("NVDA", 50, 100.0, current_price=cp)
        assert out == "User currently holds 50 shares of NVDA at $100.00 cost basis."


@pytest.mark.unit
def test_format_no_pnl_tail_when_cost_basis_is_zero():
    """A zero cost basis would make P&L undefined — skip the tail."""
    out = format_position_context("NVDA", 50, 0.0, current_price=100.0)
    assert "Current price" not in out
    assert out.endswith("cost basis.")


@pytest.mark.unit
def test_ask_position_threads_current_price_into_output():
    with patch("builtins.input", side_effect=["50", "100"]):
        out = ask_position("NVDA", current_price=130.0)
    assert "+30.0% vs cost" in out
