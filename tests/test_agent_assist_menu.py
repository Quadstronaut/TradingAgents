"""Tests for the flat-picker menu and slot collection."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tradingagents.agent_assist import menu


def _inputs(*values):
    """Patch input() to yield these values in order."""
    return patch("builtins.input", side_effect=list(values))


@pytest.mark.unit
def test_quit_returns_none():
    with _inputs("q"):
        assert menu.run_menu() is None


@pytest.mark.unit
def test_specific_ticker_flow_collects_ticker_and_confirms():
    with _inputs("1", "nvda", "y"):
        task = menu.run_menu()
    assert task is not None
    assert task.intent == "specific"
    assert task.ticker == "NVDA"


@pytest.mark.unit
def test_owned_flow_collects_all_three_slots():
    with _inputs("4", "NVDA", "50", "130", "y"):
        task = menu.run_menu()
    assert task.intent == "owned"
    assert task.ticker == "NVDA"
    assert task.shares == 50
    assert task.cost_basis == 130


@pytest.mark.unit
def test_compare_rejects_same_ticker_twice():
    with _inputs("5", "NVDA", "NVDA", "AMD", "y"):
        task = menu.run_menu()
    assert task.intent == "compare"
    assert task.ticker == "NVDA"
    assert task.ticker_b == "AMD"


@pytest.mark.unit
def test_budget_screen_collects_int_and_optional_theme():
    with _inputs("3", "100", "tech", "y"):
        task = menu.run_menu()
    assert task.intent == "budget"
    assert task.budget == 100
    assert task.theme == "tech"


@pytest.mark.unit
def test_budget_screen_with_dollar_sign_prefix_strips_it():
    with _inputs("3", "$50", "", "y"):
        task = menu.run_menu()
    assert task.budget == 50
    assert task.theme is None


@pytest.mark.unit
def test_invalid_menu_choice_reprompts_then_quits():
    with _inputs("99", "abc", "q"):
        assert menu.run_menu() is None


@pytest.mark.unit
def test_decline_confirm_returns_to_menu_not_caller():
    # Pick option 1, type a ticker, decline, then quit.
    with _inputs("1", "NVDA", "n", "q"):
        assert menu.run_menu() is None


@pytest.mark.unit
def test_back_during_slot_returns_to_menu():
    # Pick option 1, type 'b' to back out, then quit.
    with _inputs("1", "b", "q"):
        assert menu.run_menu() is None


@pytest.mark.unit
def test_news_scan_flow():
    with _inputs("6", "NVDA", "y"):
        task = menu.run_menu()
    assert task.intent == "news_scan"
    assert task.ticker == "NVDA"


@pytest.mark.unit
def test_freeform_flow_passes_prompt_through():
    with _inputs("7", "should I buy NVDA next week", "y"):
        task = menu.run_menu()
    assert task.intent == "freeform"
    assert task.prompt == "should I buy NVDA next week"


@pytest.mark.unit
def test_task_display_strings_are_human_readable():
    t = menu.Task(intent="specific", ticker="NVDA")
    assert "NVDA" in t.display()

    t = menu.Task(intent="owned", ticker="NVDA", shares=10, cost_basis=120.5)
    assert "10 shares" in t.display() or "10 " in t.display()
    assert "$120.50" in t.display()

    t = menu.Task(intent="compare", ticker="NVDA", ticker_b="AMD")
    assert "NVDA" in t.display() and "AMD" in t.display()
