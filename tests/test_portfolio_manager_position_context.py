"""Verify additional_portfolio_context flows through state into the PM prompt."""

import pytest
from langchain_core.messages import AIMessage

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.graph.propagation import Propagator


class _CapturePromptLLM:
    """Fake LLM that records the prompt and returns a stub response.

    Raises NotImplementedError from with_structured_output so the agent's
    bind_structured helper falls back to plain invoke (which we capture).
    """

    def __init__(self):
        self.captured_prompt = None

    def with_structured_output(self, schema):
        raise NotImplementedError("test fake — force freetext path")

    def invoke(self, prompt):
        self.captured_prompt = prompt
        return AIMessage(content="**Recommendation**: Hold\n\nFINAL TRANSACTION PROPOSAL: **HOLD**")


def _base_state(extra=None):
    state = {
        "company_of_interest": "NVDA",
        "investment_plan": "Bullish thesis text.",
        "trader_investment_plan": "Buy 100 shares at market.",
        "risk_debate_state": {
            "history": "Aggressive: ... Conservative: ... Neutral: ...",
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "count": 1,
        },
    }
    if extra:
        state.update(extra)
    return state


@pytest.mark.unit
def test_pm_prompt_includes_position_context_when_state_has_it():
    fake = _CapturePromptLLM()
    node = create_portfolio_manager(fake)

    state = _base_state({
        "additional_portfolio_context": "User holds 50 shares of NVDA at $130 cost basis.",
    })
    node(state)

    assert "User holds 50 shares of NVDA at $130 cost basis." in fake.captured_prompt
    assert "User-provided position context" in fake.captured_prompt


@pytest.mark.unit
def test_pm_prompt_omits_position_block_when_state_missing_key():
    fake = _CapturePromptLLM()
    node = create_portfolio_manager(fake)

    state = _base_state()  # no additional_portfolio_context key
    node(state)

    assert "User-provided position context" not in fake.captured_prompt


@pytest.mark.unit
def test_pm_prompt_omits_position_block_when_value_is_empty_string():
    fake = _CapturePromptLLM()
    node = create_portfolio_manager(fake)

    state = _base_state({"additional_portfolio_context": ""})
    node(state)

    assert "User-provided position context" not in fake.captured_prompt


@pytest.mark.unit
def test_propagator_create_initial_state_threads_position_context():
    propagator = Propagator()
    state = propagator.create_initial_state(
        company_name="NVDA",
        trade_date="2026-05-10",
        past_context="prior lessons",
        additional_portfolio_context="User holds 50 shares at $130.",
    )
    assert state["additional_portfolio_context"] == "User holds 50 shares at $130."


@pytest.mark.unit
def test_propagator_create_initial_state_defaults_position_context_to_empty():
    propagator = Propagator()
    state = propagator.create_initial_state(
        company_name="NVDA",
        trade_date="2026-05-10",
    )
    assert state["additional_portfolio_context"] == ""
