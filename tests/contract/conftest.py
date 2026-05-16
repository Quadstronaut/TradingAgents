"""Pytest configuration for cloud-LLM-client contract tests.

These tests are fully offline. They use ``unittest.mock`` to stand in for
the provider SDK boundary (the ``langchain_*`` classes that the project's
clients instantiate). Every test in this folder must carry the
``verify_contract`` marker so the suite can be selected as a group.
"""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "verify_contract: contract-style tests for cloud LLM clients (offline)",
    )
