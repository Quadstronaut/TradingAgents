"""Unit tests for the shortlist stage. All Ollama and yfinance calls are mocked."""

import datetime as _dt
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingagents.agent_assist.shortlist import (
    Candidate,
    ShortList,
    bulk_price,
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


@pytest.mark.unit
def test_shortlist_uses_precomputed_prices_and_skips_yfinance():
    """When precomputed_prices is given, _price() must not be consulted."""
    fake_llm = _llm_returning([[
        {"ticker": "INTC", "reasoning": "value"},
        {"ticker": "PLTR", "reasoning": "growth"},
    ]])
    prices = {"INTC": 22.0, "PLTR": 85.0}
    with patch("tradingagents.agent_assist.shortlist._build_llm", return_value=fake_llm), \
         patch("tradingagents.agent_assist.shortlist._price") as mock_price:
        result = shortlist(
            "tech", UNIVERSE_DF, budget=100,
            precomputed_prices=prices,
        )

    mock_price.assert_not_called()
    tickers = sorted(c.ticker for c in result)
    assert tickers == ["INTC", "PLTR"]


@pytest.mark.unit
def test_shortlist_drops_picks_missing_from_precomputed_prices():
    """LLM may name a ticker we didn't price — treat as 'no price'."""
    fake_llm = _llm_returning([[
        {"ticker": "INTC", "reasoning": "value"},
        {"ticker": "PLTR", "reasoning": "growth"},
        {"ticker": "AMD", "reasoning": "growth"},
    ]])
    prices = {"INTC": 22.0, "PLTR": 85.0}  # AMD intentionally missing
    with patch("tradingagents.agent_assist.shortlist._build_llm", return_value=fake_llm), \
         patch("tradingagents.agent_assist.shortlist._price") as mock_price:
        result = shortlist(
            "tech", UNIVERSE_DF, budget=100,
            precomputed_prices=prices,
        )

    mock_price.assert_not_called()
    tickers = sorted(c.ticker for c in result)
    assert tickers == ["INTC", "PLTR"]


# ---------------------------------------------------------------------------
# bulk_price
# ---------------------------------------------------------------------------


def _fake_bulk_download(prices: dict[str, float]):
    """Mock for shortlist._yf_bulk_download."""
    def _f(tickers):
        return {t: prices[t] for t in tickers if t in prices}
    return _f


@pytest.mark.unit
def test_bulk_price_hits_yfinance_on_first_call_and_caches(tmp_path):
    prices = {"AMD": 142.0, "INTC": 24.0, "PLTR": 87.0}
    with patch(
        "tradingagents.agent_assist.shortlist._yf_bulk_download",
        side_effect=_fake_bulk_download(prices),
    ) as bulk:
        result = bulk_price(["AMD", "INTC", "PLTR"], cache_dir=tmp_path)

    assert result == prices
    bulk.assert_called_once()
    files = list(tmp_path.glob("prices_*.json"))
    assert len(files) == 1
    with files[0].open() as f:
        cached = json.load(f)
    assert cached == {"AMD": 142.0, "INTC": 24.0, "PLTR": 87.0}


@pytest.mark.unit
def test_bulk_price_reads_cache_and_skips_yfinance_for_known_tickers(tmp_path):
    today = _dt.date.today().isoformat()
    cache_file = tmp_path / f"prices_{today}.json"
    cache_file.write_text(json.dumps({"AMD": 100.0, "INTC": 20.0}), encoding="utf-8")

    with patch(
        "tradingagents.agent_assist.shortlist._yf_bulk_download",
    ) as bulk:
        result = bulk_price(["AMD", "INTC"], cache_dir=tmp_path)

    assert result == {"AMD": 100.0, "INTC": 20.0}
    bulk.assert_not_called()


@pytest.mark.unit
def test_bulk_price_fetches_only_missing_tickers(tmp_path):
    today = _dt.date.today().isoformat()
    cache_file = tmp_path / f"prices_{today}.json"
    cache_file.write_text(json.dumps({"AMD": 100.0}), encoding="utf-8")

    captured = {}

    def _capture(tickers):
        captured["tickers"] = list(tickers)
        return {"INTC": 20.0, "PLTR": 80.0}

    with patch(
        "tradingagents.agent_assist.shortlist._yf_bulk_download",
        side_effect=_capture,
    ):
        result = bulk_price(["AMD", "INTC", "PLTR"], cache_dir=tmp_path)

    assert result == {"AMD": 100.0, "INTC": 20.0, "PLTR": 80.0}
    assert captured["tickers"] == ["INTC", "PLTR"]


@pytest.mark.unit
def test_bulk_price_returns_only_tickers_with_prices(tmp_path):
    with patch(
        "tradingagents.agent_assist.shortlist._yf_bulk_download",
        side_effect=_fake_bulk_download({"AMD": 142.0}),
    ):
        result = bulk_price(["AMD", "INTC"], cache_dir=tmp_path)

    assert result == {"AMD": 142.0}  # INTC absent — no price


@pytest.mark.unit
def test_bulk_price_handles_yesterdays_cache_as_a_miss(tmp_path):
    yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    (tmp_path / f"prices_{yesterday}.json").write_text(
        json.dumps({"AMD": 99.0}), encoding="utf-8"
    )

    with patch(
        "tradingagents.agent_assist.shortlist._yf_bulk_download",
        side_effect=_fake_bulk_download({"AMD": 142.0}),
    ) as bulk:
        result = bulk_price(["AMD"], cache_dir=tmp_path)

    assert result == {"AMD": 142.0}  # not the stale 99.0
    bulk.assert_called_once()
