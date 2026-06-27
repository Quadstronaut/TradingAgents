"""Pytest configuration for cloud-LLM-client contract tests.

These tests are fully offline. They use ``unittest.mock`` to stand in for
the provider SDK boundary (the ``langchain_*`` classes that the project's
clients instantiate). Every test in this folder must carry the
``verify_contract`` marker so the suite can be selected as a group.
"""

import contextlib
import dataclasses

from unittest.mock import MagicMock, patch

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "verify_contract: contract-style tests for cloud LLM clients (offline)",
    )


@pytest.fixture
def swap_chat_class():
    """Swap an OpenAI-compatible provider's registry ``chat_class`` for a mock.

    The OpenAI-compatible build path resolves the constructed class through
    ``OPENAI_COMPATIBLE_PROVIDERS[provider].chat_class`` — a reference captured
    into the frozen ``ProviderSpec`` at import time. Patching the module-level
    class name (``openai_client.NormalizedChatOpenAI``) does NOT rebind that
    captured reference, so the real class would still be built. We replace the
    registry entry instead, which is exactly the seam the production code reads.

    Usage::

        with swap_chat_class("xai") as chat_cls:
            client.get_llm()
        chat_cls.call_args.kwargs       # constructor kwargs
        chat_cls.return_value           # the built LLM
    """

    @contextlib.contextmanager
    def _swap(provider):
        from tradingagents.llm_clients import openai_client as oc

        mock_cls = MagicMock()
        patched_spec = dataclasses.replace(
            oc.OPENAI_COMPATIBLE_PROVIDERS[provider], chat_class=mock_cls
        )
        with patch.dict(oc.OPENAI_COMPATIBLE_PROVIDERS, {provider: patched_spec}):
            yield mock_cls

    return _swap
