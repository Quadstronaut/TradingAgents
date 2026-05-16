"""Self-tests for runner.py.

These cover the lightweight pieces (shape check, task builder, input
bypass). The deep-run path is exercised by the matrix itself; mocking
it inside a unit test would just re-test the mock.
"""

from __future__ import annotations

import pytest

from tests.verify import runner
from tradingagents.agent_assist.summarize import RunResult


@pytest.mark.unit
class TestCheckShape:
    def _md(self) -> str:
        """Sample PM markdown matching the render_pm_decision contract."""
        return (
            "**Rating**: Buy\n\n"
            "**Executive Summary**: Initiate Buy with 20% allocation.\n\n"
            "**Investment Thesis**: Strong fundamentals and rising margins.\n\n"
            "**Price Target**: 150.0\n\n"
            "**Time Horizon**: 6-12 months\n"
        )

    def test_complete_markdown_is_all_ok(self):
        r = RunResult(
            ticker="NVDA", rating="Buy", log_path=None, error=None,
            decision_md=self._md(),
        )
        report = runner._check_shape(r)
        assert report.all_ok is True

    def test_missing_executive_summary_flagged(self):
        md = self._md().replace("**Executive Summary**:", "(no summary)")
        r = RunResult("NVDA", "Buy", None, None, decision_md=md)
        report = runner._check_shape(r)
        assert report.executive_summary_present is False
        assert report.all_ok is False

    def test_missing_investment_thesis_flagged(self):
        md = self._md().replace("**Investment Thesis**:", "(no thesis)")
        r = RunResult("NVDA", "Buy", None, None, decision_md=md)
        report = runner._check_shape(r)
        assert report.investment_thesis_present is False
        assert report.all_ok is False

    def test_missing_rating_header_flagged(self):
        md = self._md().replace("**Rating**:", "(no rating)")
        r = RunResult("NVDA", "Buy", None, None, decision_md=md)
        report = runner._check_shape(r)
        assert report.rating_header_present is False
        assert report.all_ok is False

    def test_empty_markdown_fails_all(self):
        r = RunResult("NVDA", "Buy", None, None, decision_md="")
        report = runner._check_shape(r)
        assert report.rating_header_present is False
        assert "empty decision_md" in report.notes

    def test_failed_rating_flags_pydantic(self):
        r = RunResult("NVDA", "FAILED", None, "boom", decision_md=self._md())
        report = runner._check_shape(r)
        assert report.pydantic_validated is False

    def test_free_text_fallback_accepted_when_content_is_substantial(self):
        # Mimic the free-text fallback path: no headers, but the rating
        # word appears and the markdown is non-trivial.
        free_text = (
            "Portfolio Manager's Final Decision: Hold. "
            "After reviewing the risk debate, the team concluded that the "
            "balance of bull and bear arguments is roughly even. Holding the "
            "current position is appropriate while monitoring earnings and "
            "macroeconomic indicators. The bull case rests on the company's "
            "strong cash position; the bear case on debt and competitive risks."
        )
        r = RunResult("NVDA", "Hold", None, None, decision_md=free_text)
        report = runner._check_shape(r)
        assert report.structured_path_ok is False
        assert report.fallback_path_ok is True
        assert report.all_ok is True

    def test_free_text_too_short_rejected(self):
        # Same shape (no headers), but trivially short markdown — fail.
        r = RunResult("NVDA", "Hold", None, None, decision_md="Hold.")
        report = runner._check_shape(r)
        assert report.fallback_path_ok is False
        assert report.all_ok is False

    def test_free_text_without_rating_word_rejected(self):
        # Substantial markdown but rating word doesn't appear.
        body = "x" * 500
        r = RunResult("NVDA", "Hold", None, None, decision_md=body)
        report = runner._check_shape(r)
        assert report.fallback_path_ok is False
        assert report.all_ok is False


@pytest.mark.unit
class TestBuildTask:
    def test_specific(self):
        t = runner._build_task("specific", {"ticker": "NVDA"})
        assert t.intent == "specific" and t.ticker == "NVDA"

    def test_owned_uses_defaults(self):
        t = runner._build_task("owned", {"ticker": "AAPL"})
        assert t.shares == 10.0 and t.cost_basis == 100.0

    def test_compare_requires_both_tickers(self):
        t = runner._build_task("compare", {"ticker": "A", "ticker_b": "B"})
        assert t.ticker == "A" and t.ticker_b == "B"

    def test_unknown_intent_raises(self):
        with pytest.raises(ValueError):
            runner._build_task("nope", {})


@pytest.mark.unit
class TestInputBypass:
    def test_default_answer_is_y(self):
        with runner._bypass_inputs() as used:
            assert input("anything? ") == "y"
        assert used == ["y"]

    def test_scripted_consumed_in_order(self):
        with runner._bypass_inputs(["100", "5.0", "200.0"]) as used:
            a = input("budget? ")
            b = input("shares? ")
            c = input("basis? ")
        assert (a, b, c) == ("100", "5.0", "200.0")
        assert used == ["100", "5.0", "200.0"]

    def test_overflow_falls_back_to_last(self):
        with runner._bypass_inputs(["y"]) as used:
            a = input()
            b = input()
        assert a == "y" and b == "y"
        assert used == ["y", "y"]


@pytest.mark.unit
class TestPassResult:
    def _ok_shape(self, ticker: str = "NVDA"):
        return runner.ShapeReport(
            ticker=ticker,
            rating_header_present=True,
            executive_summary_present=True,
            investment_thesis_present=True,
            pydantic_validated=True,
            rating_word_in_md=True,
            markdown_chars=400,
        )

    def test_robust_ok_requires_canonical(self):
        r = runner.PassResult(
            pass_no=0, intent="specific", inputs={"ticker": "NVDA"},
            exit_code=0, ratings=["Buy"], shapes=[self._ok_shape()],
            elapsed_sec=1.0,
        )
        assert r.robust_ok is True and r.shape_ok is True

    def test_non_canonical_rating_fails_robust(self):
        r = runner.PassResult(
            pass_no=0, intent="specific", inputs={},
            exit_code=0, ratings=["Banana"], shapes=[],
            elapsed_sec=1.0,
        )
        assert r.robust_ok is False

    def test_news_scan_with_no_ratings_is_robust(self):
        r = runner.PassResult(
            pass_no=0, intent="news_scan", inputs={},
            exit_code=0, ratings=[], shapes=[],
            elapsed_sec=1.0,
        )
        assert r.robust_ok is True and r.shape_ok is True

    def test_error_fails_robust(self):
        r = runner.PassResult(
            pass_no=0, intent="specific", inputs={},
            exit_code=99, ratings=[], shapes=[],
            elapsed_sec=1.0, error="boom",
        )
        assert r.robust_ok is False
