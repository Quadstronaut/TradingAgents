"""Contract tests for the DeepSeek LLM client.

DeepSeek uses the OpenAIClient with a dedicated DeepSeekChatOpenAI
subclass to handle thinking-mode quirks (reasoning_content round-trip
and tool_choice suppression for V4 / reasoner models).

Structured output:
- ``deepseek-chat`` (V3.2 non-thinking) -> function_calling WITH tool_choice
- ``deepseek-reasoner`` / ``deepseek-v4-*`` -> function_calling WITHOUT tool_choice
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
def test_create_llm_client_returns_openai_client_for_deepseek():
    client = create_llm_client(provider="deepseek", model="deepseek-chat")
    assert client.__class__.__name__ == "OpenAIClient"
    assert client.provider == "deepseek"


@pytest.mark.verify_contract
def test_get_llm_uses_deepseek_subclass_endpoint_and_key(monkeypatch, swap_chat_class):
    """DeepSeek must be wired to the DeepSeekChatOpenAI subclass (not the base
    NormalizedChatOpenAI) and hit api.deepseek.com with the resolved key.
    """
    from tradingagents.llm_clients import openai_client as oc

    # The provider registry is the single source of truth for the subclass: the
    # DeepSeek row must point at DeepSeekChatOpenAI (thinking-mode quirks).
    assert oc.OPENAI_COMPATIBLE_PROVIDERS["deepseek"].chat_class is oc.DeepSeekChatOpenAI

    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test-key")

    with swap_chat_class("deepseek") as mock_cls:
        client = create_llm_client(provider="deepseek", model="deepseek-chat")
        client.get_llm()

    mock_cls.assert_called_once()
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["model"] == "deepseek-chat"
    assert kwargs["base_url"] == "https://api.deepseek.com"
    assert kwargs.get("api_key") == "ds-test-key"


@pytest.mark.verify_contract
def test_bind_structured_for_deepseek_chat_sends_tool_choice():
    """deepseek-chat supports tool_choice; bind_structured must NOT suppress it."""
    from tradingagents.llm_clients import openai_client as oc

    with patch.object(oc.ChatOpenAI, "__init__", return_value=None):
        with patch.object(
            oc.ChatOpenAI, "with_structured_output", return_value=MagicMock(name="structured")
        ) as mock_with:
            llm = oc.NormalizedChatOpenAI.__new__(oc.NormalizedChatOpenAI)
            object.__setattr__(llm, "model_name", "deepseek-chat")
            bind_structured(llm, _Schema, agent_name="test-agent")

    args, kwargs = mock_with.call_args
    assert args[0] is _Schema
    assert kwargs.get("method") == "function_calling"
    # supports_tool_choice=True means client does NOT set tool_choice=None.
    assert "tool_choice" not in kwargs


@pytest.mark.verify_contract
def test_bind_structured_for_deepseek_reasoner_suppresses_tool_choice():
    """deepseek-reasoner rejects tool_choice; bind_structured must send tool_choice=None."""
    from tradingagents.llm_clients import openai_client as oc

    with patch.object(oc.ChatOpenAI, "__init__", return_value=None):
        with patch.object(
            oc.ChatOpenAI, "with_structured_output", return_value=MagicMock(name="structured")
        ) as mock_with:
            llm = oc.NormalizedChatOpenAI.__new__(oc.NormalizedChatOpenAI)
            object.__setattr__(llm, "model_name", "deepseek-reasoner")
            bind_structured(llm, _Schema, agent_name="test-agent")

    args, kwargs = mock_with.call_args
    assert args[0] is _Schema
    assert kwargs.get("method") == "function_calling"
    # supports_tool_choice=False means client explicitly sets tool_choice=None.
    assert kwargs.get("tool_choice", "MISSING") is None


@pytest.mark.verify_contract
def test_bind_structured_for_deepseek_v4_pro_suppresses_tool_choice():
    """deepseek-v4-pro (thinking model) also requires tool_choice suppression."""
    from tradingagents.llm_clients import openai_client as oc

    with patch.object(oc.ChatOpenAI, "__init__", return_value=None):
        with patch.object(
            oc.ChatOpenAI, "with_structured_output", return_value=MagicMock(name="structured")
        ) as mock_with:
            llm = oc.NormalizedChatOpenAI.__new__(oc.NormalizedChatOpenAI)
            object.__setattr__(llm, "model_name", "deepseek-v4-pro")
            bind_structured(llm, _Schema, agent_name="test-agent")

    args, kwargs = mock_with.call_args
    assert kwargs.get("tool_choice", "MISSING") is None


@pytest.mark.verify_contract
def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        create_llm_client(provider="deep-seek-wrong", model="x")


@pytest.mark.verify_contract
def test_unknown_model_emits_warning_but_succeeds():
    with patch(
        "tradingagents.llm_clients.openai_client.DeepSeekChatOpenAI",
        return_value=MagicMock(),
    ):
        client = create_llm_client(provider="deepseek", model="deepseek-imaginary-id")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client.get_llm()

    messages = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
    assert any("deepseek-imaginary-id" in m and "deepseek" in m for m in messages)
