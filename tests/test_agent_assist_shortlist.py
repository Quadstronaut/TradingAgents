"""Unit tests for the shortlist stage. All Ollama and yfinance calls are mocked."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingagents.agent_assist.shortlist import (
    Candidate,
    ShortList,
    shortlist,
)


UNIVERSE_DF = pd.DataFrame([
    {"ticker": "AMD", "name": "Advanced Micro Devices", "sector": "Tech", "industry": "Semis"},
    {"ticker": "INTC", "name": "Intel", "sector": "Tech", "industry": "Semis"},
    {"ticker": "PLTR", "name": "Palantir", "sector": "Tech", "industry": "Software"},
    {"ticker": "AAL", "name": "American Airlines", "sector": "Industrials", "industry": "Airlines"},
])


def _llm_returning(candidates_per_call):
    """Build a fake structured LLM whose .invoke returns ShortList objects in order."""
    responses = [ShortList(candidates=[Candidate(**c) for c in batch]) for batch in candidates_per_call]
    fake = MagicMock()
    fake.invoke.side_effect = responses
    return fake


def _mock_price(prices: dict[str, float | None]):
    """Build a `_price()` replacement that returns prices from the dict.

    None means the price call failed (yfinance error, dropped candidate).
    """
    def _p(ticker: str):
        return prices.get(ticker)
    return _p


@pytest.mark.unit
def test_shortlist_returns_only_candidates_under_budget():
    fake_llm = _llm_returning([[
        {"ticker": "AMD", "reasoning": "growth"},
        {"ticker": "INTC", "reasoning": "value"},
        {"ticker": "PLTR", "reasoning": "momentum"},
    ]])
    with patch("tradingagents.agent_assist.shortlist._build_llm", return_value=fake_llm), \
         patch("tradingagents.agent_assist.shortlist._price",
               side_effect=_mock_price({"AMD": 142.0, "INTC": 24.0, "PLTR": 87.0})):
        result = shortlist("tech under $100", UNIVERSE_DF, budget=100)

    tickers = [c.ticker for c in result]
    assert "AMD" not in tickers   # 142 > 100, dropped
    assert "INTC" in tickers
    assert "PLTR" in tickers


@pytest.mark.unit
def test_shortlist_drops_candidates_yfinance_cannot_price():
    # 3 initial candidates so 2 survive after 1 yfinance failure — no reprompt needed.
    fake_llm = _llm_returning([[
        {"ticker": "AMD", "reasoning": "x"},
        {"ticker": "INTC", "reasoning": "y"},
        {"ticker": "PLTR", "reasoning": "z"},
    ]])
    with patch("tradingagents.agent_assist.shortlist._build_llm", return_value=fake_llm), \
         patch("tradingagents.agent_assist.shortlist._price",
               side_effect=_mock_price({"AMD": None, "INTC": 24.0, "PLTR": 87.0})):
        result = shortlist("tech", UNIVERSE_DF, budget=None)

    tickers = [c.ticker for c in result]
    assert "AMD" not in tickers
    assert "INTC" in tickers
    assert "PLTR" in tickers


@pytest.mark.unit
def test_shortlist_reprompts_once_when_survivors_below_two():
    # First call: AMD ($142), INTC ($24) → only INTC survives ($100 cap) → reprompt
    # Second call: AAL ($14), PLTR ($87) → both survive
    fake_llm = _llm_returning([
        [{"ticker": "AMD", "reasoning": "x"}, {"ticker": "INTC", "reasoning": "y"}],
        [{"ticker": "AAL", "reasoning": "z"}, {"ticker": "PLTR", "reasoning": "w"}],
    ])
    with patch("tradingagents.agent_assist.shortlist._build_llm", return_value=fake_llm), \
         patch("tradingagents.agent_assist.shortlist._price",
               side_effect=_mock_price({"AMD": 142.0, "INTC": 24.0, "AAL": 14.0, "PLTR": 87.0})):
        result = shortlist("tech under $100", UNIVERSE_DF, budget=100)

    tickers = sorted(c.ticker for c in result)
    assert tickers == ["AAL", "INTC", "PLTR"]
    assert fake_llm.invoke.call_count == 2  # original + one reprompt


@pytest.mark.unit
def test_shortlist_returns_empty_when_no_survivors_after_reprompt():
    # Both passes return candidates priced over budget — nothing survives.
    fake_llm = _llm_returning([
        [{"ticker": "AMD", "reasoning": "x"}, {"ticker": "PLTR", "reasoning": "y"}],
        [{"ticker": "INTC", "reasoning": "z"}, {"ticker": "AAL", "reasoning": "w"}],
    ])
    with patch("tradingagents.agent_assist.shortlist._build_llm", return_value=fake_llm), \
         patch("tradingagents.agent_assist.shortlist._price",
               side_effect=_mock_price({"AMD": 142.0, "PLTR": 187.0,
                                        "INTC": 124.0, "AAL": 114.0})):
        result = shortlist("tech under $100", UNIVERSE_DF, budget=100)

    assert result == []


@pytest.mark.unit
def test_shortlist_skips_price_filter_when_budget_is_none():
    fake_llm = _llm_returning([[
        {"ticker": "AMD", "reasoning": "x"},
        {"ticker": "INTC", "reasoning": "y"},
    ]])
    with patch("tradingagents.agent_assist.shortlist._build_llm", return_value=fake_llm), \
         patch("tradingagents.agent_assist.shortlist._price",
               side_effect=_mock_price({"AMD": 999.0, "INTC": 24.0})):
        result = shortlist("any tech", UNIVERSE_DF, budget=None)

    tickers = sorted(c.ticker for c in result)
    assert tickers == ["AMD", "INTC"]
