"""Unit tests for the news-scan flow's dataflow config scoping and verdict capture.

The news-scan flow runs as a function call (not a long-lived object) but
mutates the dataflows.config module-level state via ``set_config`` so that
analyst tools (``get_news``, ``get_reddit_sentiment``, etc.) pick up the
correct provider routing. These tests pin the contract that the global
state is restored on exit, so back-to-back tasks in the same process
don't see leaked config from a prior news scan.

A second batch of tests covers the typed-verdict capture path: the
terminal tail summary used to index ``verdict_md.splitlines()[2]`` which
broke silently if the verdict renderer ever shifted lines. The capture
list pattern reads the typed object back directly.
"""

from unittest.mock import MagicMock

import pytest

from tradingagents.agent_assist.news_scan import (
    NewsVerdict,
    _build_dataflow_config,
    _create_verdict_node,
    _render_verdict,
    _scoped_dataflow_config,
)
from tradingagents.dataflows.config import get_config, set_config


@pytest.fixture(autouse=True)
def restore_dataflow_config_after_test():
    """Belt-and-braces: snapshot + restore around every test so a failing
    assertion can't pollute later tests in the same process."""
    snapshot = get_config()
    yield
    set_config(snapshot)


@pytest.mark.unit
def test_scoped_dataflow_config_installs_cfg_inside_block():
    inside = {}
    test_cfg = {
        "llm_provider": "test-provider-xyz",
        "deep_think_llm": "test-deep-model",
    }
    with _scoped_dataflow_config(test_cfg):
        inside = get_config()
    assert inside["llm_provider"] == "test-provider-xyz"
    assert inside["deep_think_llm"] == "test-deep-model"


@pytest.mark.unit
def test_scoped_dataflow_config_restores_state_on_normal_exit():
    initial = get_config()
    test_cfg = {"llm_provider": "test-provider", "deep_think_llm": "test-deep"}
    with _scoped_dataflow_config(test_cfg):
        pass
    after = get_config()
    assert after["llm_provider"] == initial["llm_provider"]
    assert after["deep_think_llm"] == initial["deep_think_llm"]


@pytest.mark.unit
def test_scoped_dataflow_config_restores_state_on_exception():
    """Whatever raises inside the block, restore must still happen."""
    initial = get_config()
    test_cfg = {"llm_provider": "should-not-leak"}
    with pytest.raises(RuntimeError, match="boom"):
        with _scoped_dataflow_config(test_cfg):
            raise RuntimeError("boom")
    after = get_config()
    assert after["llm_provider"] == initial["llm_provider"]


@pytest.mark.unit
def test_scoped_dataflow_config_nests_correctly():
    """Inner scope restores to outer scope's value, not to the absolute initial."""
    initial = get_config()
    outer_cfg = {"llm_provider": "outer"}
    inner_cfg = {"llm_provider": "inner"}
    with _scoped_dataflow_config(outer_cfg):
        with _scoped_dataflow_config(inner_cfg):
            assert get_config()["llm_provider"] == "inner"
        # After inner exit, outer scope is active again.
        assert get_config()["llm_provider"] == "outer"
    # After outer exit, fully restored.
    assert get_config()["llm_provider"] == initial["llm_provider"]


@pytest.mark.unit
def test_build_dataflow_config_overrides_only_provider_fields():
    """The cfg we install should change only the four LLM-routing fields and
    leave the rest of DEFAULT_CONFIG (vendor routing, dirs, debate rounds)
    untouched — analysts depend on those remaining stable."""
    cfg = _build_dataflow_config()
    from tradingagents.default_config import DEFAULT_CONFIG
    for k, v in DEFAULT_CONFIG.items():
        if k in {"llm_provider", "backend_url", "deep_think_llm", "quick_think_llm"}:
            continue
        assert cfg[k] == v, f"unexpected change to '{k}': {v!r} -> {cfg[k]!r}"


# ---------------------------------------------------------------------------
# Verdict rendering and typed-verdict capture
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_render_verdict_contains_lean_confidence_and_summary():
    v = NewsVerdict(lean="bullish", confidence=0.75, summary="Strong tailwinds.")
    out = _render_verdict(v, "NVDA")
    assert "## News & sentiment scan for NVDA" in out
    assert "**Lean:** bullish" in out
    assert "**Confidence:** 0.75" in out
    assert "Strong tailwinds." in out
    assert "not a buy/hold/sell recommendation" in out


@pytest.mark.unit
def test_render_verdict_formats_confidence_to_two_decimals():
    """Two decimal places — the tail print depends on the rendered form
    matching what users see in the markdown file."""
    v = NewsVerdict(lean="neutral", confidence=0.333, summary="Mixed signals.")
    assert "**Confidence:** 0.33" in _render_verdict(v, "AAPL")


@pytest.mark.unit
def test_verdict_node_appends_to_capture_list():
    """The capture closure is how the caller reads the typed verdict back
    without parsing markdown. This is the fix for the splitlines()[2]
    fragility — the typed object travels alongside, not through, the
    rendered string."""
    canned = NewsVerdict(lean="bearish", confidence=0.6, summary="Headwinds.")
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = canned

    captured: list[NewsVerdict] = []
    node = _create_verdict_node(fake_llm, captured)
    state = {
        "company_of_interest": "INTC",
        "sentiment_report": "social blob",
        "news_report": "news blob",
        "messages": [],
    }
    result = node(state)

    assert captured == [canned]
    # Rendered string is still in state.final_trade_decision for the file
    assert "INTC" in result["final_trade_decision"]
    assert "**Lean:** bearish" in result["final_trade_decision"]
    assert "**Confidence:** 0.60" in result["final_trade_decision"]


@pytest.mark.unit
def test_verdict_node_accumulates_multiple_invocations_in_capture():
    """If the graph runs the Verdict node more than once (e.g., a retry),
    capture accumulates. The caller reads ``captured[-1]`` — the latest
    verdict wins."""
    fake_llm = MagicMock()
    v1 = NewsVerdict(lean="neutral", confidence=0.4, summary="first")
    v2 = NewsVerdict(lean="bullish", confidence=0.8, summary="second")
    fake_llm.with_structured_output.return_value.invoke.side_effect = [v1, v2]

    captured: list[NewsVerdict] = []
    node = _create_verdict_node(fake_llm, captured)
    state = {
        "company_of_interest": "MSFT",
        "sentiment_report": "",
        "news_report": "",
        "messages": [],
    }
    node(state)
    node(state)
    assert captured == [v1, v2]
    assert captured[-1] is v2
