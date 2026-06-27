"""Contract tests for the Google Gemini LLM client.

Offline. ``langchain_google_genai.ChatGoogleGenerativeAI`` is mocked at
the client-module boundary.

Gemini uses ``response_schema`` (JSON Schema in the
``generationConfig.response_schema`` field) for structured output, exposed
through ChatGoogleGenerativeAI's ``with_structured_output`` default
binding. The project's ``NormalizedChatGoogleGenerativeAI`` does not
override that method, so we verify the schema is forwarded as-is.
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
def test_create_llm_client_returns_google_client():
    client = create_llm_client(provider="google", model="gemini-2.5-flash")
    assert client.__class__.__name__ == "GoogleClient"
    assert client.model == "gemini-2.5-flash"


@pytest.mark.verify_contract
def test_get_llm_instantiates_normalized_chat_google():
    sentinel = MagicMock(name="GeminiInstance")
    with patch(
        "tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI",
        return_value=sentinel,
    ) as mock_cls:
        client = create_llm_client(
            provider="google",
            model="gemini-2.5-flash",
            api_key="test-key",
        )
        result = client.get_llm()

    assert result is sentinel
    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args.kwargs
    assert call_kwargs["model"] == "gemini-2.5-flash"
    # The client remaps unified ``api_key`` to provider-specific ``google_api_key``.
    assert call_kwargs.get("google_api_key") == "test-key"


@pytest.mark.verify_contract
def test_thinking_level_no_longer_maps_to_thinking_budget():
    """The integer ``thinking_budget`` (the Gemini-2.5 knob) was retired upstream
    in v0.3.0: every model now takes the raw string ``thinking_level``. A
    2.5-era model id must therefore pass ``thinking_level`` through unchanged and
    must NOT synthesize a ``thinking_budget`` kwarg.
    """
    with patch(
        "tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI",
        return_value=MagicMock(),
    ) as mock_cls:
        client = create_llm_client(
            provider="google",
            model="gemini-2.5-pro",
            thinking_level="high",
        )
        client.get_llm()

    kwargs = mock_cls.call_args.kwargs
    assert kwargs.get("thinking_level") == "high"
    assert "thinking_budget" not in kwargs


@pytest.mark.verify_contract
def test_thinking_level_passes_through_for_gemini_3():
    """Gemini 3 keeps thinking_level as the raw API parameter."""
    with patch(
        "tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI",
        return_value=MagicMock(),
    ) as mock_cls:
        client = create_llm_client(
            provider="google",
            model="gemini-3-flash-preview",
            thinking_level="medium",
        )
        client.get_llm()

    kwargs = mock_cls.call_args.kwargs
    assert kwargs.get("thinking_level") == "medium"


@pytest.mark.verify_contract
def test_bind_structured_uses_response_schema_shape():
    """ChatGoogleGenerativeAI's default with_structured_output binds via
    Gemini's ``response_schema`` (JSON Schema). We verify the schema is
    forwarded with no overriding ``method`` kwarg.
    """
    from tradingagents.llm_clients import google_client as gc

    mock_bound = MagicMock(name="gemini-structured")
    with patch.object(gc.ChatGoogleGenerativeAI, "__init__", return_value=None):
        with patch.object(
            gc.ChatGoogleGenerativeAI,
            "with_structured_output",
            return_value=mock_bound,
        ) as mock_with:
            llm = gc.NormalizedChatGoogleGenerativeAI.__new__(
                gc.NormalizedChatGoogleGenerativeAI
            )
            bound = bind_structured(llm, _Schema, agent_name="test-agent")

    assert bound is mock_bound
    mock_with.assert_called_once()
    args, kwargs = mock_with.call_args
    assert args[0] is _Schema
    # No method override — Gemini's binding decides the wire format.
    assert "method" not in kwargs


@pytest.mark.verify_contract
def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        create_llm_client(provider="not-google", model="gemini-x")


@pytest.mark.verify_contract
def test_unknown_model_emits_warning_but_succeeds():
    with patch(
        "tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI",
        return_value=MagicMock(),
    ):
        client = create_llm_client(provider="google", model="gemini-not-a-real-id")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client.get_llm()

    messages = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
    assert any("gemini-not-a-real-id" in m and "google" in m for m in messages)
