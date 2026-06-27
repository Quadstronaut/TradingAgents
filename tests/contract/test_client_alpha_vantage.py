"""Contract tests for the Alpha Vantage data vendor.

Offline. ``requests.get`` is mocked at the
``tradingagents.dataflows.alpha_vantage_common`` boundary so no network
calls are issued and no real API key is required.

What we lock in here:
1. ``route_to_vendor("get_stock_data", ...)`` resolves to the
   Alpha Vantage implementation when ``data_vendors`` is configured for it.
2. The stock fetcher parses the CSV response and returns a date-filtered
   CSV string with the expected columns (timestamp / open / high / low /
   close / volume — Alpha Vantage's TIME_SERIES_DAILY_ADJUSTED shape).
3. The news fetcher passes the JSON response through unchanged.
4. A rate-limit response raises ``AlphaVantageRateLimitError``.
"""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingagents.dataflows.alpha_vantage_common import AlphaVantageRateLimitError


# ---------------------------------------------------------------------------
# Inline fixtures (recorded shape, scrubbed to a handful of rows).
# ---------------------------------------------------------------------------

# TIME_SERIES_DAILY_ADJUSTED returns CSV with this header when datatype=csv.
_STOCK_CSV_FIXTURE = (
    "timestamp,open,high,low,close,adjusted_close,volume,dividend_amount,split_coefficient\n"
    "2024-01-05,180.0,182.5,179.0,181.2,181.2,52000000,0.0,1.0\n"
    "2024-01-04,178.0,181.0,177.5,180.5,180.5,48000000,0.0,1.0\n"
    "2024-01-03,176.5,179.0,176.0,178.0,178.0,55000000,0.0,1.0\n"
    "2024-01-02,175.0,177.0,174.5,176.0,176.0,50000000,0.0,1.0\n"
    # Out of range — must be filtered out:
    "2023-12-29,170.0,172.0,169.0,171.5,171.5,45000000,0.0,1.0\n"
)

# NEWS_SENTIMENT returns JSON.
_NEWS_JSON_FIXTURE = {
    "items": "2",
    "sentiment_score_definition": "x:y:z",
    "relevance_score_definition": "a:b:c",
    "feed": [
        {
            "title": "Acme Corp beats Q4 expectations",
            "url": "https://example.com/news/1",
            "time_published": "20240105T140000",
            "summary": "Acme posted record revenue.",
            "overall_sentiment_score": 0.42,
            "overall_sentiment_label": "Bullish",
            "ticker_sentiment": [
                {"ticker": "ACME", "ticker_sentiment_score": "0.5"},
            ],
        },
        {
            "title": "Acme Corp guidance cut",
            "url": "https://example.com/news/2",
            "time_published": "20240104T103000",
            "summary": "Acme lowered FY guidance.",
            "overall_sentiment_score": -0.21,
            "overall_sentiment_label": "Somewhat-Bearish",
            "ticker_sentiment": [
                {"ticker": "ACME", "ticker_sentiment_score": "-0.3"},
            ],
        },
    ],
}

_RATE_LIMIT_JSON_FIXTURE = {
    "Information": (
        "Thank you for using Alpha Vantage! Our standard API rate limit is "
        "25 requests per day. Please subscribe to a premium API key."
    )
}


def _mock_response(*, text: str | None = None, json_dict: dict | None = None) -> MagicMock:
    """Build a MagicMock that mimics ``requests.Response``."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.status_code = 200
    if text is not None:
        resp.text = text
    elif json_dict is not None:
        resp.text = json.dumps(json_dict)
    else:
        resp.text = ""
    return resp


# ---------------------------------------------------------------------------
# 1. Routing: Alpha Vantage selected when configured.
# ---------------------------------------------------------------------------


@pytest.mark.verify_contract
def test_route_to_vendor_picks_alpha_vantage_when_configured(monkeypatch):
    """Configuring ``data_vendors.core_stock_apis = alpha_vantage`` must
    dispatch ``get_stock_data`` through the alpha_vantage implementation.
    """
    from tradingagents.dataflows import interface as iface

    captured = {"called": False, "args": None, "kwargs": None}

    def fake_alpha(symbol, start_date, end_date, *args, **kwargs):
        captured["called"] = True
        captured["args"] = (symbol, start_date, end_date)
        return "ALPHA_VANTAGE_RESULT"

    # Route table: swap the alpha_vantage entry for our spy.
    monkeypatch.setitem(
        iface.VENDOR_METHODS["get_stock_data"],
        "alpha_vantage",
        fake_alpha,
    )
    # Force the routing config to alpha_vantage for this method's category.
    monkeypatch.setattr(
        iface,
        "get_config",
        lambda: {
            "data_vendors": {"core_stock_apis": "alpha_vantage"},
            "tool_vendors": {},
        },
    )

    result = iface.route_to_vendor("get_stock_data", "ACME", "2024-01-01", "2024-01-05")
    assert result == "ALPHA_VANTAGE_RESULT"
    assert captured["called"] is True
    assert captured["args"] == ("ACME", "2024-01-01", "2024-01-05")


@pytest.mark.verify_contract
def test_tool_vendor_override_beats_category_default(monkeypatch):
    """Per-tool ``tool_vendors`` configuration overrides the category default."""
    from tradingagents.dataflows import interface as iface

    captured = {"called": False}

    def fake_alpha(symbol, start_date, end_date, *args, **kwargs):
        captured["called"] = True
        return "from-alpha"

    monkeypatch.setitem(
        iface.VENDOR_METHODS["get_stock_data"],
        "alpha_vantage",
        fake_alpha,
    )
    # Category default says yfinance, but tool override says alpha_vantage.
    monkeypatch.setattr(
        iface,
        "get_config",
        lambda: {
            "data_vendors": {"core_stock_apis": "yfinance"},
            "tool_vendors": {"get_stock_data": "alpha_vantage"},
        },
    )

    result = iface.route_to_vendor("get_stock_data", "ACME", "2024-01-01", "2024-01-05")
    assert result == "from-alpha"
    assert captured["called"] is True


# ---------------------------------------------------------------------------
# 2. Stock fetcher: parses CSV, filters by date range, preserves columns.
# ---------------------------------------------------------------------------


@pytest.mark.verify_contract
def test_get_stock_returns_csv_with_expected_columns(monkeypatch):
    """get_stock() must return a CSV string whose columns match the Alpha
    Vantage TIME_SERIES_DAILY_ADJUSTED schema.
    """
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-key")

    from tradingagents.dataflows.alpha_vantage_stock import get_stock

    with patch(
        "tradingagents.dataflows.alpha_vantage_common.requests.get",
        return_value=_mock_response(text=_STOCK_CSV_FIXTURE),
    ) as mock_get:
        result_csv = get_stock("ACME", "2024-01-02", "2024-01-05")

    mock_get.assert_called_once()
    # The request hits Alpha Vantage's /query endpoint with the right function.
    call_args, call_kwargs = mock_get.call_args
    assert call_args[0] == "https://www.alphavantage.co/query"
    params = call_kwargs["params"]
    assert params["function"] == "TIME_SERIES_DAILY_ADJUSTED"
    assert params["symbol"] == "ACME"
    assert params["datatype"] == "csv"
    assert params["apikey"] == "test-key"

    # Parse the returned CSV and verify schema + filtering.
    df = pd.read_csv(StringIO(result_csv))
    expected_columns = {
        "timestamp", "open", "high", "low", "close",
        "adjusted_close", "volume", "dividend_amount", "split_coefficient",
    }
    assert set(df.columns) == expected_columns

    # Rows outside [2024-01-02, 2024-01-05] must be filtered out.
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    assert df["timestamp"].min() >= pd.Timestamp("2024-01-02")
    assert df["timestamp"].max() <= pd.Timestamp("2024-01-05")
    # 4 rows in range from the fixture.
    assert len(df) == 4


# ---------------------------------------------------------------------------
# 3. News fetcher: passes JSON response through, hits the right endpoint.
# ---------------------------------------------------------------------------


@pytest.mark.verify_contract
def test_get_news_returns_json_text_and_uses_news_sentiment_function(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-key")

    from tradingagents.dataflows.alpha_vantage_news import get_news

    with patch(
        "tradingagents.dataflows.alpha_vantage_common.requests.get",
        return_value=_mock_response(json_dict=_NEWS_JSON_FIXTURE),
    ) as mock_get:
        result = get_news("ACME", "2024-01-01", "2024-01-05")

    # The wire function is NEWS_SENTIMENT with the ticker filter.
    params = mock_get.call_args.kwargs["params"]
    assert params["function"] == "NEWS_SENTIMENT"
    assert params["tickers"] == "ACME"
    # Dates are reformatted to YYYYMMDDTHHMM.
    assert params["time_from"].startswith("20240101T")
    assert params["time_to"].startswith("20240105T")

    # Response body is returned as raw JSON text.
    decoded = json.loads(result)
    assert decoded["feed"][0]["title"] == "Acme Corp beats Q4 expectations"


# ---------------------------------------------------------------------------
# 4. Rate-limit JSON -> AlphaVantageRateLimitError.
# ---------------------------------------------------------------------------


@pytest.mark.verify_contract
def test_rate_limit_response_raises_alpha_vantage_rate_limit_error(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-key")

    from tradingagents.dataflows.alpha_vantage_stock import get_stock

    with patch(
        "tradingagents.dataflows.alpha_vantage_common.requests.get",
        return_value=_mock_response(json_dict=_RATE_LIMIT_JSON_FIXTURE),
    ):
        with pytest.raises(AlphaVantageRateLimitError, match="rate limit"):
            get_stock("ACME", "2024-01-02", "2024-01-05")


@pytest.mark.verify_contract
def test_route_to_vendor_falls_back_on_rate_limit(monkeypatch):
    """When the primary in a *configured* multi-vendor chain rate-limits,
    route_to_vendor must fall back to the next vendor in that chain.

    Fallback is now scoped to the vendors the user explicitly chained
    (``data_vendors="alpha_vantage,yfinance"``); routing no longer silently
    reaches for a vendor that wasn't configured (#988/#289). So both vendors
    are listed here, in order, to exercise the fallback.
    """
    from tradingagents.dataflows import interface as iface

    def rate_limited(*args, **kwargs):
        raise AlphaVantageRateLimitError("rate limit exceeded")

    yf_calls = {"count": 0}

    def fake_yfinance(*args, **kwargs):
        yf_calls["count"] += 1
        return "yfinance-fallback"

    monkeypatch.setitem(
        iface.VENDOR_METHODS["get_stock_data"],
        "alpha_vantage",
        rate_limited,
    )
    monkeypatch.setitem(
        iface.VENDOR_METHODS["get_stock_data"],
        "yfinance",
        fake_yfinance,
    )
    monkeypatch.setattr(
        iface,
        "get_config",
        lambda: {
            "data_vendors": {"core_stock_apis": "alpha_vantage,yfinance"},
            "tool_vendors": {},
        },
    )

    result = iface.route_to_vendor("get_stock_data", "ACME", "2024-01-01", "2024-01-05")
    assert result == "yfinance-fallback"
    assert yf_calls["count"] == 1


# ---------------------------------------------------------------------------
# 5. API-key validation: missing key surfaces a clear error.
# ---------------------------------------------------------------------------


@pytest.mark.verify_contract
def test_missing_api_key_raises_value_error(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)

    from tradingagents.dataflows.alpha_vantage_stock import get_stock

    # The conftest autouse fixture pre-fills the env var, but we deleted it
    # above; the request must fail before any network call.
    with patch(
        "tradingagents.dataflows.alpha_vantage_common.requests.get"
    ) as mock_get:
        with pytest.raises(ValueError, match="ALPHA_VANTAGE_API_KEY"):
            get_stock("ACME", "2024-01-02", "2024-01-05")

    mock_get.assert_not_called()
