"""Unit tests for the head-to-head compare flow.

run_compare is invoked via the orchestrator with ``deep_runner`` and
``summary_writer`` injected, so we drive these tests by handing in
fakes — no LLM or yfinance call leaks out.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agent_assist.compare import (
    ComparisonVerdict,
    run_compare,
)
from tradingagents.agent_assist.summarize import RunResult


def _runner_returning(*results: RunResult):
    """Build a deep_runner stub that returns ``results`` in order."""
    it = iter(results)

    def _deep(ticker, *, today, **_kwargs):
        return next(it)
    return _deep


def _ok_result(ticker: str, rating: str = "Buy") -> RunResult:
    return RunResult(
        ticker=ticker,
        rating=rating,
        log_path=Path(f"/tmp/{ticker}"),
        error=None,
        decision_md=f"FINAL TRANSACTION PROPOSAL: **{rating.upper()}**",
    )


def _failed_result(ticker: str) -> RunResult:
    return RunResult(
        ticker=ticker, rating="FAILED", log_path=None, error="boom",
    )


@pytest.mark.unit
def test_compare_returns_0_when_both_runs_succeed(tmp_path):
    summary_writer = MagicMock(return_value=tmp_path / "ranked.md")
    runner = _runner_returning(_ok_result("AAPL"), _ok_result("MSFT", "Hold"))
    with patch(
        "tradingagents.agent_assist.compare._summarize_comparison",
        return_value=ComparisonVerdict(winner="A", reasoning="AAPL wins"),
    ):
        rc = run_compare(
            "AAPL", "MSFT",
            output_dir=tmp_path,
            deep_runner=runner,
            summary_writer=summary_writer,
        )
    assert rc == 0


@pytest.mark.unit
def test_compare_returns_4_when_first_run_failed(tmp_path):
    summary_writer = MagicMock(return_value=tmp_path / "ranked.md")
    runner = _runner_returning(_failed_result("AAPL"), _ok_result("MSFT"))
    # Comparison summariser shouldn't even be called on FAILED runs (graph
    # short-circuits to a tie verdict).
    with patch(
        "tradingagents.agent_assist.compare._summarize_comparison",
    ) as _summ:
        rc = run_compare(
            "AAPL", "MSFT",
            output_dir=tmp_path,
            deep_runner=runner,
            summary_writer=summary_writer,
        )
    assert rc == 4
    _summ.assert_not_called()


@pytest.mark.unit
def test_compare_returns_4_when_second_run_failed(tmp_path):
    summary_writer = MagicMock(return_value=tmp_path / "ranked.md")
    runner = _runner_returning(_ok_result("AAPL"), _failed_result("MSFT"))
    with patch(
        "tradingagents.agent_assist.compare._summarize_comparison",
    ):
        rc = run_compare(
            "AAPL", "MSFT",
            output_dir=tmp_path,
            deep_runner=runner,
            summary_writer=summary_writer,
        )
    assert rc == 4


@pytest.mark.unit
def test_compare_returns_4_when_both_runs_failed(tmp_path):
    summary_writer = MagicMock(return_value=tmp_path / "ranked.md")
    runner = _runner_returning(_failed_result("AAPL"), _failed_result("MSFT"))
    with patch(
        "tradingagents.agent_assist.compare._summarize_comparison",
    ):
        rc = run_compare(
            "AAPL", "MSFT",
            output_dir=tmp_path,
            deep_runner=runner,
            summary_writer=summary_writer,
        )
    assert rc == 4


@pytest.mark.unit
def test_compare_writes_summary_even_on_partial_failure(tmp_path):
    """The summary file is still useful — it documents which run failed.
    Exit code reflects the analysis, not the write."""
    summary_writer = MagicMock(return_value=tmp_path / "ranked.md")
    runner = _runner_returning(_failed_result("AAPL"), _ok_result("MSFT"))
    with patch(
        "tradingagents.agent_assist.compare._summarize_comparison",
    ):
        run_compare(
            "AAPL", "MSFT",
            output_dir=tmp_path,
            deep_runner=runner,
            summary_writer=summary_writer,
        )
    # Compare-specific markdown is always written
    compare_files = list(tmp_path.glob("*-compare-*.md"))
    assert len(compare_files) == 1
    # Standard ranked summary writer is invoked
    summary_writer.assert_called_once()


@pytest.mark.unit
def test_compare_rejects_path_traversal_ticker(tmp_path):
    """A direct call (not via run_task) with a dangerous ticker must not
    interpolate it into the output path. safe_ticker_component raises."""
    summary_writer = MagicMock(return_value=tmp_path / "ranked.md")
    runner = _runner_returning(_ok_result("AAPL"), _ok_result("MSFT"))
    with patch(
        "tradingagents.agent_assist.compare._summarize_comparison",
        return_value=ComparisonVerdict(winner="A", reasoning="x"),
    ):
        with pytest.raises(ValueError, match="characters not allowed"):
            run_compare(
                "../etc/foo", "MSFT",
                output_dir=tmp_path,
                deep_runner=runner,
                summary_writer=summary_writer,
            )


@pytest.mark.unit
def test_compare_summariser_failure_still_returns_4_if_runs_failed(tmp_path):
    """If the comparison summariser raises, the function logs and
    continues with a 'tie' verdict. But if either deep run failed, the
    exit code must still reflect that — the summariser failure is on top."""
    summary_writer = MagicMock(return_value=tmp_path / "ranked.md")
    runner = _runner_returning(_failed_result("AAPL"), _ok_result("MSFT"))
    with patch(
        "tradingagents.agent_assist.compare._summarize_comparison",
        side_effect=RuntimeError("LLM down"),
    ):
        rc = run_compare(
            "AAPL", "MSFT",
            output_dir=tmp_path,
            deep_runner=runner,
            summary_writer=summary_writer,
        )
    assert rc == 4
