"""Contract tests for the Anthropic LLM client.

Offline. ``langchain_anthropic.ChatAnthropic`` is mocked at the
client-module boundary so no network calls are issued.

Anthropic uses tool-use for structured output. The project's
``NormalizedChatAnthropic`` does not override ``with_structured_output``,
so the call lands on the inherited ChatAnthropic implementation which
binds the schema as an Anthropic tool.
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
def test_create_llm_client_returns_anthropic_client():
    client = create_llm_client(provider="anthropic", model="claude-sonnet-4-6")
    assert client.__class__.__name__ == "AnthropicClient"
    assert client.model == "claude-sonnet-4-6"


@pytest.mark.verify_contract
def test_get_llm_instantiates_normalized_chat_anthropic():
    """get_llm() constructs NormalizedChatAnthropic with the model name."""
    sentinel = MagicMock(name="ChatAnthropicInstance")
    with patch(
        "tradingagents.llm_clients.anthropic_client.NormalizedChatAnthropic",
        return_value=sentinel,
    ) as mock_cls:
        client = create_llm_client(
            provider="anthropic",
            model="claude-sonnet-4-6",
            max_tokens=1024,
        )
        result = client.get_llm()

    assert result is sentinel
    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    # Pass-through kwargs flow into the underlying LLM constructor.
    assert call_kwargs.get("max_tokens") == 1024


@pytest.mark.verify_contract
def test_get_llm_forwards_base_url_when_provided():
    """Custom base_url (corporate proxy) must reach the underlying LLM kwargs."""
    with patch(
        "tradingagents.llm_clients.anthropic_client.NormalizedChatAnthropic",
        return_value=MagicMock(),
    ) as mock_cls:
        client = create_llm_client(
            provider="anthropic",
            model="claude-sonnet-4-6",
            base_url="https://proxy.example.com/v1",
        )
        client.get_llm()

    assert mock_cls.call_args.kwargs.get("base_url") == "https://proxy.example.com/v1"


@pytest.mark.verify_contract
def test_bind_structured_uses_tool_use_shape():
    """NormalizedChatAnthropic does not override with_structured_output; it
    inherits Anthropic's tool-use binding. We assert the schema reaches the
    underlying method without a forced method override.
    """
    from tradingagents.llm_clients import anthropic_client as ac

    mock_bound = MagicMock(name="anthropic-structured")
    with patch.object(ac.ChatAnthropic, "__init__", return_value=None):
        with patch.object(
            ac.ChatAnthropic, "with_structured_output", return_value=mock_bound
        ) as mock_with:
            llm = ac.NormalizedChatAnthropic.__new__(ac.NormalizedChatAnthropic)
            bound = bind_structured(llm, _Schema, agent_name="test-agent")

    assert bound is mock_bound
    mock_with.assert_called_once()
    args, kwargs = mock_with.call_args
    assert args[0] is _Schema
    # The project does not override ``method`` for Anthropic — Anthropic's
    # ChatAnthropic defaults to its tool-use binding (the only supported
    # path on Claude). So no explicit ``method`` should be forwarded.
    assert "method" not in kwargs


@pytest.mark.verify_contract
def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        create_llm_client(provider="nonexistent-provider", model="claude-x")


@pytest.mark.verify_contract
def test_unknown_model_emits_warning_but_succeeds():
    """Anthropic warns on unknown model IDs but still constructs the client."""
    with patch(
        "tradingagents.llm_clients.anthropic_client.NormalizedChatAnthropic",
        return_value=MagicMock(),
    ):
        client = create_llm_client(provider="anthropic", model="claude-not-a-real-id")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client.get_llm()

    messages = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
    assert any("claude-not-a-real-id" in m and "anthropic" in m for m in messages)
