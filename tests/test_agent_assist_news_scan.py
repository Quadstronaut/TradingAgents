"""Unit tests for the news-scan flow's dataflow config scoping.

The news-scan flow runs as a function call (not a long-lived object) but
mutates the dataflows.config module-level state via ``set_config`` so that
analyst tools (``get_news``, ``get_reddit_sentiment``, etc.) pick up the
correct provider routing. These tests pin the contract that the global
state is restored on exit, so back-to-back tasks in the same process
don't see leaked config from a prior news scan.
"""

import pytest

from tradingagents.agent_assist.news_scan import (
    _build_dataflow_config,
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
