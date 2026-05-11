"""Unit tests for prompt classification."""

import pytest

from tradingagents.agent_assist.prompt_parse import ParsedPrompt, parse_prompt


# Universe used by the parser to validate ticker candidates. Keep small and
# deterministic so we don't depend on the bundled CSV in unit tests.
UNIVERSE = {"AAPL", "MSFT", "NVDA", "AMD", "INTC", "PLTR", "BRK-B", "META", "GOOGL"}


@pytest.mark.unit
@pytest.mark.parametrize("prompt,expected_tickers,expected_intent,expected_hold", [
    ("should I buy AAPL", ["AAPL"], "single", False),
    ("Should I sell NVDA or wait?", ["NVDA"], "single", True),
    ("compare AMD vs INTC", ["AMD", "INTC"], "multi", False),
    ("dump my MSFT?", ["MSFT"], "single", True),
    ("what tech companies can I get into with $100", [], "screen", False),
    ("anything good to buy with $50", [], "screen", False),
    ("research me some semiconductor stocks", [], "screen", False),
    ("BRK-B worth holding?", ["BRK-B"], "single", True),
])
def test_parse_prompt_classifies_correctly(prompt, expected_tickers, expected_intent, expected_hold):
    result = parse_prompt(prompt, universe=UNIVERSE)
    assert result.tickers == expected_tickers
    assert result.intent == expected_intent
    assert result.hold_intent == expected_hold


@pytest.mark.unit
@pytest.mark.parametrize("prompt", [
    "USA stocks under $100",                   # USA is a word, not in universe
    "any good ETF picks",                       # ETF is a word, not in universe
    "should I BUY something",                   # BUY is the word, not a ticker
])
def test_parse_prompt_rejects_false_positive_ticker_words(prompt):
    result = parse_prompt(prompt, universe=UNIVERSE)
    assert result.tickers == []
    assert result.intent == "screen"


@pytest.mark.unit
def test_parse_prompt_returns_named_tuple_fields():
    result = parse_prompt("buy AAPL", universe=UNIVERSE)
    assert isinstance(result, ParsedPrompt)
    assert result.tickers == ["AAPL"]
    assert result.intent == "single"
    assert result.hold_intent is False
