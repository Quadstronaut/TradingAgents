"""Contract tests for the xAI (Grok) LLM client.

xAI rides on the OpenAI-compatible client: the project's OpenAIClient
points NormalizedChatOpenAI at api.x.ai with the XAI_API_KEY. Structured
output uses function_calling (OpenAI tool-calling) by default.
"""

from __future__ import annotations

import os
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
def test_create_llm_client_returns_openai_client_for_xai():
    """xAI shares the OpenAIClient implementation."""
    client = create_llm_client(provider="xai", model="grok-4-0709")
    assert client.__class__.__name__ == "OpenAIClient"
    assert client.provider == "xai"


@pytest.mark.verify_contract
def test_get_llm_uses_xai_endpoint_and_api_key(monkeypatch, swap_chat_class):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")

    with swap_chat_class("xai") as mock_cls:
        client = create_llm_client(provider="xai", model="grok-4-0709")
        client.get_llm()

    kwargs = mock_cls.call_args.kwargs
    assert kwargs["model"] == "grok-4-0709"
    # xAI default endpoint.
    assert kwargs["base_url"] == "https://api.x.ai/v1"
    assert kwargs.get("api_key") == "xai-test-key"
    # Third-party OpenAI-compatible providers must NOT enable Responses API.
    assert kwargs.get("use_responses_api") is None or kwargs.get("use_responses_api") is False


@pytest.mark.verify_contract
def test_explicit_base_url_overrides_default(swap_chat_class):
    """Corporate proxy / gateway: user-supplied base_url wins over default."""
    with swap_chat_class("xai") as mock_cls:
        client = create_llm_client(
            provider="xai",
            model="grok-4-0709",
            base_url="https://internal-gw.example.com/xai",
        )
        client.get_llm()
    assert mock_cls.call_args.kwargs["base_url"] == "https://internal-gw.example.com/xai"


@pytest.mark.verify_contract
def test_bind_structured_uses_function_calling_method():
    from tradingagents.llm_clients import openai_client as oc

    with patch.object(oc.ChatOpenAI, "__init__", return_value=None):
        with patch.object(
            oc.ChatOpenAI, "with_structured_output", return_value=MagicMock(name="structured")
        ) as mock_with:
            llm = oc.NormalizedChatOpenAI.__new__(oc.NormalizedChatOpenAI)
            object.__setattr__(llm, "model_name", "grok-4-0709")
            bound = bind_structured(llm, _Schema, agent_name="test-agent")

    assert bound is not None
    args, kwargs = mock_with.call_args
    assert args[0] is _Schema
    assert kwargs.get("method") == "function_calling"


@pytest.mark.verify_contract
def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        create_llm_client(provider="grok-ai", model="grok-x")


@pytest.mark.verify_contract
def test_unknown_model_emits_warning_but_succeeds():
    with patch(
        "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI",
        return_value=MagicMock(),
    ):
        client = create_llm_client(provider="xai", model="grok-imaginary")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client.get_llm()

    messages = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
    assert any("grok-imaginary" in m and "xai" in m for m in messages)
