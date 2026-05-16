"""Contract tests for the OpenAI LLM client.

These tests are offline. The provider SDK (``langchain_openai.ChatOpenAI``)
is mocked at the client-module boundary so no network calls happen and no
real API key is required.

What we lock in here:
1. ``create_llm_client(provider="openai", ...)`` returns an object whose
   ``.get_llm()`` constructs the project's ``NormalizedChatOpenAI`` subclass
   with the right kwargs (model, ``use_responses_api=True``).
2. ``bind_structured`` -> ``llm.with_structured_output(schema)`` forwards
   to the parent class with ``method="function_calling"`` (per the model
   capability table for OpenAI defaults).
3. Negative tests: unknown provider raises ``ValueError``; unknown model
   for the OpenAI provider emits a ``RuntimeWarning`` but does not raise.
"""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from tradingagents.agents.utils.structured import bind_structured
from tradingagents.llm_clients.factory import create_llm_client


class _Schema(BaseModel):
    """Minimal Pydantic schema for structured-output binding tests."""

    decision: str
    confidence: float


@pytest.mark.verify_contract
def test_create_llm_client_returns_openai_client():
    """Factory builds an OpenAIClient for provider='openai'."""
    client = create_llm_client(provider="openai", model="gpt-5.4")
    assert client.__class__.__name__ == "OpenAIClient"
    assert client.model == "gpt-5.4"
    assert client.provider == "openai"


@pytest.mark.verify_contract
def test_get_llm_instantiates_normalized_chat_openai():
    """get_llm() constructs NormalizedChatOpenAI with use_responses_api=True for native OpenAI."""
    sentinel = MagicMock(name="ChatOpenAIInstance")
    with patch(
        "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI",
        return_value=sentinel,
    ) as mock_cls:
        client = create_llm_client(provider="openai", model="gpt-5.4")
        result = client.get_llm()

    assert result is sentinel
    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args.kwargs
    assert call_kwargs["model"] == "gpt-5.4"
    # Native OpenAI must use the Responses API.
    assert call_kwargs.get("use_responses_api") is True


@pytest.mark.verify_contract
def test_get_llm_does_not_set_base_url_for_native_openai():
    """Native OpenAI must not pre-fill base_url unless the user provides one."""
    with patch(
        "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI",
        return_value=MagicMock(),
    ) as mock_cls:
        client = create_llm_client(provider="openai", model="gpt-5.4")
        client.get_llm()
    # Native OpenAI does not appear in _PROVIDER_CONFIG so no base_url is set.
    assert "base_url" not in mock_cls.call_args.kwargs


@pytest.mark.verify_contract
def test_bind_structured_uses_function_calling_method():
    """OpenAI default capability is ``function_calling``; bind_structured forwards it."""
    # Build a real NormalizedChatOpenAI instance with a fully-mocked __init__
    # and patch the inherited ``with_structured_output`` to observe args.
    from tradingagents.llm_clients import openai_client as oc

    with patch.object(oc.ChatOpenAI, "__init__", return_value=None):
        with patch.object(
            oc.ChatOpenAI, "with_structured_output", return_value=MagicMock(name="structured")
        ) as mock_with:
            llm = oc.NormalizedChatOpenAI.__new__(oc.NormalizedChatOpenAI)
            # model_name is consulted by the capability lookup.
            object.__setattr__(llm, "model_name", "gpt-5.4")
            bound = bind_structured(llm, _Schema, agent_name="test-agent")

    assert bound is not None
    mock_with.assert_called_once()
    # Positional schema and keyword method must be present.
    args, kwargs = mock_with.call_args
    assert args[0] is _Schema
    assert kwargs.get("method") == "function_calling"


@pytest.mark.verify_contract
def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        create_llm_client(provider="totally-fake-provider", model="x")


@pytest.mark.verify_contract
def test_unknown_model_emits_warning_but_succeeds():
    """OpenAI strict provider warns but does not raise for unknown model IDs."""
    with patch(
        "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI",
        return_value=MagicMock(),
    ):
        client = create_llm_client(provider="openai", model="not-a-real-openai-model")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client.get_llm()

    messages = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
    assert any("not-a-real-openai-model" in m and "openai" in m for m in messages)
