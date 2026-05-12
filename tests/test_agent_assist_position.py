"""Unit tests for position-context prompting. input() is mocked.

The rendered string must match the format produced by
``orchestrator._run_owned`` so the LLM sees a single canonical
position-context shape regardless of how the position was collected.
"""

from unittest.mock import patch

import pytest

from tradingagents.agent_assist.position import ask_position


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
    from the same shares + basis values."""
    shares = 100.0
    basis = 142.75
    ticker = "AMD"
    with patch("builtins.input", side_effect=[str(shares), str(basis)]):
        ask_position_output = ask_position(ticker)

    run_owned_template = (
        f"User currently holds {shares:g} shares of {ticker} "
        f"at ${basis:.2f} cost basis."
    )
    assert ask_position_output == run_owned_template
