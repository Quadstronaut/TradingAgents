"""Unit tests for prompt classification."""

import pytest

from tradingagents.agent_assist.prompt_parse import (
    ParsedPrompt,
    normalize_ticker,
    parse_prompt,
)


# Universe used by the parser to validate ticker candidates. Keep small and
# deterministic so we don't depend on the bundled CSV in unit tests.
UNIVERSE = {"AAPL", "MSFT", "NVDA", "AMD", "INTC", "PLTR", "BRK-B", "META", "GOOGL"}


@pytest.mark.unit
@pytest.mark.parametrize("prompt,expected_tickers,expected_intent,expected_hold", [
    ("should I buy AAPL", ("AAPL",), "single", False),
    ("Should I sell NVDA or wait?", ("NVDA",), "single", True),
    ("compare AMD vs INTC", ("AMD", "INTC"), "multi", False),
    ("dump my MSFT?", ("MSFT",), "single", True),
    ("what tech companies can I get into with $100", (), "screen", False),
    ("anything good to buy with $50", (), "screen", False),
    ("research me some semiconductor stocks", (), "screen", False),
    ("BRK-B worth holding?", ("BRK-B",), "single", True),
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
    assert result.tickers == ()
    assert result.intent == "screen"


@pytest.mark.unit
def test_parse_prompt_returns_parsed_prompt_fields():
    result = parse_prompt("buy AAPL", universe=UNIVERSE)
    assert isinstance(result, ParsedPrompt)
    assert result.tickers == ("AAPL",)
    assert result.intent == "single"
    assert result.hold_intent is False


@pytest.mark.unit
@pytest.mark.parametrize("prompt", [
    "I'm awaiting news on AAPL",   # 'wait' inside 'awaiting' should not trigger
    "the bookkeeper handles MSFT", # 'keep' inside 'bookkeeper' should not trigger
])
def test_hold_keywords_require_word_boundaries(prompt):
    result = parse_prompt(prompt, universe=UNIVERSE)
    assert result.hold_intent is False


# ---------------------------------------------------------------------------
# normalize_ticker: single source of truth for ticker canonicalisation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("nvda", "NVDA"),
    ("NVDA", "NVDA"),
    ("brk.b", "BRK-B"),
    ("BRK.B", "BRK-B"),
    ("BRK-B", "BRK-B"),          # already canonical → idempotent
    ("brk-b", "BRK-B"),
    ("  AAPL  ", "AAPL"),         # surrounding whitespace stripped
    ("\tBF.B\n", "BF-B"),
])
def test_normalize_ticker_canonicalises_inputs(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.unit
def test_normalize_ticker_is_idempotent():
    """f(f(x)) == f(x) for every input the codebase produces."""
    for raw in ["nvda", "brk.b", "BRK-B", "  amd  ", "msft"]:
        once = normalize_ticker(raw)
        twice = normalize_ticker(once)
        assert once == twice


# ---------------------------------------------------------------------------
# parse_prompt: case-insensitive ticker recognition
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("prompt,expected_tickers,expected_intent", [
    ("should i buy nvda", ("NVDA",), "single"),
    ("Should I sell aapl or wait", ("AAPL",), "single"),
    ("brk.b vs aapl", ("BRK-B", "AAPL"), "multi"),
    ("BRK.b vs aapl", ("BRK-B", "AAPL"), "multi"),     # mixed case
    ("compare amd vs intc vs nvda", ("AMD", "INTC", "NVDA"), "multi"),
])
def test_parse_prompt_recognises_lowercase_tickers(
    prompt, expected_tickers, expected_intent,
):
    """Users naturally type lowercase. The parser used to be case-sensitive
    and quietly mis-routed lowercase prompts to the screen path."""
    result = parse_prompt(prompt, universe=UNIVERSE)
    assert result.tickers == expected_tickers
    assert result.intent == expected_intent
