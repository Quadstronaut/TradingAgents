"""Contract tests for the Azure OpenAI client.

Offline. ``langchain_openai.AzureChatOpenAI`` is mocked at the
client-module boundary.

Azure accepts any deployed model name (validate_model always returns
True), so we do not expect a model warning for unrecognised IDs. We do
verify that the unknown-provider path still raises.
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
def test_create_llm_client_returns_azure_client():
    client = create_llm_client(provider="azure", model="my-gpt-deployment")
    assert client.__class__.__name__ == "AzureOpenAIClient"
    assert client.model == "my-gpt-deployment"


@pytest.mark.verify_contract
def test_get_llm_instantiates_normalized_azure_chat_openai(monkeypatch):
    """get_llm() builds AzureChatOpenAI with model + azure_deployment kwargs."""
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME", "prod-deployment")

    sentinel = MagicMock(name="AzureChatOpenAIInstance")
    with patch(
        "tradingagents.llm_clients.azure_client.NormalizedAzureChatOpenAI",
        return_value=sentinel,
    ) as mock_cls:
        client = create_llm_client(provider="azure", model="my-model")
        result = client.get_llm()

    assert result is sentinel
    mock_cls.assert_called_once()
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["model"] == "my-model"
    assert kwargs["azure_deployment"] == "prod-deployment"


@pytest.mark.verify_contract
def test_get_llm_falls_back_to_model_name_when_deployment_env_absent(monkeypatch):
    """If AZURE_OPENAI_DEPLOYMENT_NAME is unset, model name doubles as deployment."""
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT_NAME", raising=False)

    with patch(
        "tradingagents.llm_clients.azure_client.NormalizedAzureChatOpenAI",
        return_value=MagicMock(),
    ) as mock_cls:
        client = create_llm_client(provider="azure", model="raw-model-id")
        client.get_llm()

    assert mock_cls.call_args.kwargs["azure_deployment"] == "raw-model-id"


@pytest.mark.verify_contract
def test_bind_structured_uses_function_calling_method():
    """AzureChatOpenAI inherits from ChatOpenAI; its with_structured_output
    accepts a schema. The project's NormalizedAzureChatOpenAI does NOT
    override the method, so the call lands on the OpenAI tool-calling path
    (function_calling) by default.
    """
    from tradingagents.llm_clients import azure_client as ac

    mock_bound = MagicMock(name="azure-structured")
    with patch.object(ac.AzureChatOpenAI, "__init__", return_value=None):
        with patch.object(
            ac.AzureChatOpenAI,
            "with_structured_output",
            return_value=mock_bound,
        ) as mock_with:
            llm = ac.NormalizedAzureChatOpenAI.__new__(ac.NormalizedAzureChatOpenAI)
            bound = bind_structured(llm, _Schema, agent_name="test-agent")

    assert bound is mock_bound
    mock_with.assert_called_once()
    args, kwargs = mock_with.call_args
    assert args[0] is _Schema
    # No method override — Azure uses the underlying OpenAI default.
    assert "method" not in kwargs


@pytest.mark.verify_contract
def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        create_llm_client(provider="not-azure", model="x")


@pytest.mark.verify_contract
def test_azure_accepts_any_model_without_warning():
    """Azure's validate_model() always returns True (any deployment name is valid)."""
    with patch(
        "tradingagents.llm_clients.azure_client.NormalizedAzureChatOpenAI",
        return_value=MagicMock(),
    ):
        client = create_llm_client(provider="azure", model="totally-custom-deployment-id")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client.get_llm()

    runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    assert runtime_warnings == []
