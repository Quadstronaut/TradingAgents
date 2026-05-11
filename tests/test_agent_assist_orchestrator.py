"""Orchestrator wiring tests. Every external call (LLM, yfinance, propagate, input) is mocked."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingagents.agent_assist.orchestrator import main
from tradingagents.agent_assist.shortlist import PricedCandidate


UNIVERSE_DF = pd.DataFrame([
    {"ticker": "NVDA", "name": "NVIDIA", "sector": "Tech", "industry": "Semis"},
    {"ticker": "AMD", "name": "Advanced Micro Devices", "sector": "Tech", "industry": "Semis"},
    {"ticker": "INTC", "name": "Intel", "sector": "Tech", "industry": "Semis"},
])


@pytest.fixture
def fake_graph():
    """Patches TradingAgentsGraph so propagate() returns canned ratings without LLM calls."""
    fake = MagicMock()
    fake.propagate.return_value = ({}, "**Recommendation**: Buy\nFINAL TRANSACTION PROPOSAL: **BUY**")
    with patch(
        "tradingagents.agent_assist.orchestrator.TradingAgentsGraph",
        return_value=fake,
    ):
        yield fake


@pytest.fixture
def universe_loader():
    with patch(
        "tradingagents.agent_assist.orchestrator.load_universe",
        return_value=UNIVERSE_DF,
    ):
        yield


@pytest.mark.unit
def test_single_ticker_skips_shortlist_and_runs_propagate_once(
    fake_graph, universe_loader, tmp_path
):
    inputs = ["", "y"]  # ask_position: skip count; per-ticker confirm: y
    with patch("builtins.input", side_effect=inputs):
        rc = main(prompt="should I buy NVDA", budget=None, output_dir=tmp_path)

    assert rc == 0
    assert fake_graph.propagate.call_count == 1
    args, kwargs = fake_graph.propagate.call_args
    assert args[0] == "NVDA"
    assert kwargs.get("additional_portfolio_context", "") == ""


@pytest.mark.unit
def test_screen_intent_triggers_shortlist_then_per_ticker_confirms(
    fake_graph, universe_loader, tmp_path
):
    fake_shortlist = [
        PricedCandidate(ticker="AMD", reasoning="growth", last_price=142.0),
        PricedCandidate(ticker="INTC", reasoning="value", last_price=24.0),
    ]
    inputs = ["100", "y", "s"]  # budget interactive: 100; AMD: y; INTC: skip
    with patch("tradingagents.agent_assist.orchestrator.shortlist", return_value=fake_shortlist), \
         patch("builtins.input", side_effect=inputs):
        rc = main(prompt="what tech to buy", budget=None, output_dir=tmp_path)

    assert rc == 0
    assert fake_graph.propagate.call_count == 1
    args, _ = fake_graph.propagate.call_args
    assert args[0] == "AMD"


@pytest.mark.unit
def test_per_ticker_abort_breaks_loop_immediately(
    fake_graph, universe_loader, tmp_path
):
    fake_shortlist = [
        PricedCandidate(ticker="AMD", reasoning="x", last_price=100.0),
        PricedCandidate(ticker="INTC", reasoning="y", last_price=24.0),
    ]
    inputs = ["", "a"]  # budget skip; AMD: abort
    with patch("tradingagents.agent_assist.orchestrator.shortlist", return_value=fake_shortlist), \
         patch("builtins.input", side_effect=inputs):
        rc = main(prompt="tech anything", budget=None, output_dir=tmp_path)

    assert rc == 0
    assert fake_graph.propagate.call_count == 0


@pytest.mark.unit
def test_position_context_threaded_into_propagate_when_provided(
    fake_graph, universe_loader, tmp_path
):
    inputs = ["50", "130", "y"]  # ask_position: 50, 130; per-ticker confirm: y
    with patch("builtins.input", side_effect=inputs):
        rc = main(prompt="should I sell NVDA", budget=None, output_dir=tmp_path)

    assert rc == 0
    assert fake_graph.propagate.call_count == 1
    _, kwargs = fake_graph.propagate.call_args
    ctx = kwargs.get("additional_portfolio_context", "")
    assert "50 shares of NVDA" in ctx
    assert "$130" in ctx


@pytest.mark.unit
def test_propagate_exception_marks_failed_and_continues(
    fake_graph, universe_loader, tmp_path
):
    fake_shortlist = [
        PricedCandidate(ticker="AMD", reasoning="x", last_price=100.0),
        PricedCandidate(ticker="INTC", reasoning="y", last_price=24.0),
    ]
    fake_graph.propagate.side_effect = [
        RuntimeError("boom"),
        ({}, "**Recommendation**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**"),
    ]
    inputs = ["", "y", "y"]  # budget skip; AMD: y (will fail); INTC: y
    with patch("tradingagents.agent_assist.orchestrator.shortlist", return_value=fake_shortlist), \
         patch("builtins.input", side_effect=inputs):
        rc = main(prompt="tech anything", budget=None, output_dir=tmp_path)

    assert rc == 0
    assert fake_graph.propagate.call_count == 2
    summaries = list(tmp_path.glob("*.md"))
    assert len(summaries) == 1
    content = summaries[0].read_text(encoding="utf-8")
    assert "FAILED" in content
    assert "boom" in content


@pytest.mark.unit
def test_explicit_budget_arg_skips_interactive_budget_prompt(
    fake_graph, universe_loader, tmp_path
):
    fake_shortlist = [
        PricedCandidate(ticker="AMD", reasoning="x", last_price=24.0),
        PricedCandidate(ticker="INTC", reasoning="y", last_price=24.0),
    ]
    inputs = ["s", "s"]  # both per-ticker prompts: skip. NO budget prompt expected.
    with patch("tradingagents.agent_assist.orchestrator.shortlist", return_value=fake_shortlist) as m, \
         patch("builtins.input", side_effect=inputs):
        rc = main(prompt="what tech to buy", budget=50, output_dir=tmp_path)

    assert rc == 0
    _, kwargs = m.call_args
    assert kwargs.get("budget") == 50
