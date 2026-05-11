"""Unit tests for position-context prompting. input() is mocked."""

from unittest.mock import patch

import pytest

from tradingagents.agent_assist.position import ask_position


@pytest.mark.unit
def test_returns_formatted_string_when_both_inputs_given():
    with patch("builtins.input", side_effect=["50", "130"]):
        result = ask_position("NVDA")
    assert result == "User currently holds 50 shares of NVDA at $130 cost basis."


@pytest.mark.unit
def test_accepts_decimal_cost_basis():
    with patch("builtins.input", side_effect=["10", "127.45"]):
        result = ask_position("AAPL")
    assert result == "User currently holds 10 shares of AAPL at $127.45 cost basis."


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
    assert result == "User currently holds 50 shares of NVDA at $130 cost basis."
