"""Contract tests for the OpenRouter LLM client.

OpenRouter rides on the OpenAI-compatible client, pointing at
openrouter.ai with the OPENROUTER_API_KEY. Unlike strict providers,
OpenRouter accepts any model ID without a warning (its catalog is
fetched dynamically and the validator returns True for all models).
"""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from tradingagents.agents.utils.structured import bind_structured
from tradingagents.llm_clients.factory import create_llm_client


class _Schema(BaseModel):
    decision: str
    confidence: float


@pytest.mark.verify_contract
def test_create_llm_client_returns_openai_client_for_openrouter():
    client = create_llm_client(provider="openrouter", model="anthropic/claude-sonnet-4")
    assert client.__class__.__name__ == "OpenAIClient"
    assert client.provider == "openrouter"


@pytest.mark.verify_contract
def test_get_llm_uses_openrouter_endpoint_and_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")

    with patch(
        "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI",
        return_value=MagicMock(),
    ) as mock_cls:
        client = create_llm_client(
            provider="openrouter",
            model="openai/gpt-4o-mini",
        )
        client.get_llm()

    kwargs = mock_cls.call_args.kwargs
    assert kwargs["model"] == "openai/gpt-4o-mini"
    assert kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert kwargs.get("api_key") == "or-test-key"


@pytest.mark.verify_contract
def test_bind_structured_uses_function_calling_method():
    from tradingagents.llm_clients import openai_client as oc

    with patch.object(oc.ChatOpenAI, "__init__", return_value=None):
        with patch.object(
            oc.ChatOpenAI, "with_structured_output", return_value=MagicMock(name="structured")
        ) as mock_with:
            llm = oc.NormalizedChatOpenAI.__new__(oc.NormalizedChatOpenAI)
            object.__setattr__(llm, "model_name", "openai/gpt-4o-mini")
            bind_structured(llm, _Schema, agent_name="test-agent")

    args, kwargs = mock_with.call_args
    assert args[0] is _Schema
    assert kwargs.get("method") == "function_calling"


@pytest.mark.verify_contract
def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        create_llm_client(provider="open-router", model="x/y")


@pytest.mark.verify_contract
def test_openrouter_accepts_any_model_without_warning():
    """OpenRouter's validator returns True for any model; no warning expected."""
    with patch(
        "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI",
        return_value=MagicMock(),
    ):
        client = create_llm_client(provider="openrouter", model="some/exotic-model-id")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client.get_llm()

    runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    assert runtime_warnings == []
