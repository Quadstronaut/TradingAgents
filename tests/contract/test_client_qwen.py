"""Contract tests for the Qwen / DashScope LLM client.

Qwen rides on the OpenAI-compatible client, pointing at Alibaba Cloud's
DashScope-compatible endpoint with the DASHSCOPE_API_KEY env var.
Structured output uses function_calling.
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
def test_create_llm_client_returns_openai_client_for_qwen():
    client = create_llm_client(provider="qwen", model="qwen-plus")
    assert client.__class__.__name__ == "OpenAIClient"
    assert client.provider == "qwen"


@pytest.mark.verify_contract
def test_get_llm_uses_dashscope_endpoint_and_key(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-test-key")

    with patch(
        "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI",
        return_value=MagicMock(),
    ) as mock_cls:
        client = create_llm_client(provider="qwen", model="qwen-plus")
        client.get_llm()

    kwargs = mock_cls.call_args.kwargs
    assert kwargs["model"] == "qwen-plus"
    assert kwargs["base_url"] == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert kwargs.get("api_key") == "qwen-test-key"


@pytest.mark.verify_contract
def test_bind_structured_uses_function_calling_method():
    from tradingagents.llm_clients import openai_client as oc

    with patch.object(oc.ChatOpenAI, "__init__", return_value=None):
        with patch.object(
            oc.ChatOpenAI, "with_structured_output", return_value=MagicMock(name="structured")
        ) as mock_with:
            llm = oc.NormalizedChatOpenAI.__new__(oc.NormalizedChatOpenAI)
            object.__setattr__(llm, "model_name", "qwen-plus")
            bind_structured(llm, _Schema, agent_name="test-agent")

    args, kwargs = mock_with.call_args
    assert args[0] is _Schema
    assert kwargs.get("method") == "function_calling"


@pytest.mark.verify_contract
def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        create_llm_client(provider="qwenn", model="qwen-x")


@pytest.mark.verify_contract
def test_unknown_model_emits_warning_but_succeeds():
    with patch(
        "tradingagents.llm_clients.openai_client.NormalizedChatOpenAI",
        return_value=MagicMock(),
    ):
        client = create_llm_client(provider="qwen", model="qwen-imaginary-id")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client.get_llm()

    messages = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
    assert any("qwen-imaginary-id" in m and "qwen" in m for m in messages)
