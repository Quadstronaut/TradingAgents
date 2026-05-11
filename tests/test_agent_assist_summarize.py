"""Unit tests for summary rendering."""

from pathlib import Path

import pytest

from tradingagents.agent_assist.summarize import (
    RunResult,
    rank_results,
    write_summary,
)


@pytest.mark.unit
def test_rank_results_orders_buy_first_sell_last():
    results = [
        RunResult(ticker="A", rating="Hold", log_path=None, error=None),
        RunResult(ticker="B", rating="Buy", log_path=None, error=None),
        RunResult(ticker="C", rating="Sell", log_path=None, error=None),
        RunResult(ticker="D", rating="Overweight", log_path=None, error=None),
        RunResult(ticker="E", rating="Underweight", log_path=None, error=None),
    ]
    ranked = rank_results(results)
    assert [r.ticker for r in ranked] == ["B", "D", "A", "E", "C"]


@pytest.mark.unit
def test_rank_results_puts_skipped_and_failed_at_end():
    results = [
        RunResult(ticker="A", rating="SKIPPED", log_path=None, error=None),
        RunResult(ticker="B", rating="Buy", log_path=None, error=None),
        RunResult(ticker="C", rating="FAILED", log_path=None, error="boom"),
    ]
    ranked = rank_results(results)
    assert [r.ticker for r in ranked] == ["B", "A", "C"]


@pytest.mark.unit
def test_write_summary_creates_file_with_table_and_per_ticker_sections(tmp_path):
    results = [
        RunResult(ticker="NVDA", rating="Buy",
                  log_path=Path("~/.tradingagents/logs/NVDA"), error=None),
        RunResult(ticker="INTC", rating="Hold",
                  log_path=Path("~/.tradingagents/logs/INTC"), error=None),
        RunResult(ticker="AMD", rating="FAILED",
                  log_path=None, error="propagate raised TimeoutError"),
    ]
    out = write_summary(
        prompt="should I buy something tech",
        results=results,
        output_dir=tmp_path,
    )

    assert out.exists()
    content = out.read_text(encoding="utf-8")

    # Header captures the original prompt.
    assert "should I buy something tech" in content
    # Ranked table is present and Buy outranks Hold outranks FAILED.
    assert content.index("NVDA") < content.index("INTC") < content.index("AMD")
    # Failure rows surface the error.
    assert "propagate raised TimeoutError" in content


@pytest.mark.unit
def test_write_summary_filename_includes_timestamp_and_slug(tmp_path):
    results = [RunResult(ticker="NVDA", rating="Buy", log_path=None, error=None)]
    out = write_summary(
        prompt="What about NVDA?",
        results=results,
        output_dir=tmp_path,
    )
    # YYYYMMDD-HHMMSS-<slug>.md  e.g. 20260510-143022-what-about-nvda.md
    name = out.name
    assert name.endswith(".md")
    assert "what-about-nvda" in name
