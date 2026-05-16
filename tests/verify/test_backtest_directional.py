"""Stage-2c: directional-sanity backtest test.

Slow (5 deep runs ~= 75 minutes against Ollama). Marked ``slow`` and
``verify_directional`` so default pytest skips it; the matrix runner
invokes ``run_backtest`` directly.
"""

from __future__ import annotations

import pytest

from tests.verify import backtest


@pytest.mark.unit
class TestDatasetLoad:
    """Cheap: just confirms the CSV is well-formed."""

    def test_has_five_rows(self):
        rows = backtest.load_dataset()
        assert len(rows) == 5

    def test_each_row_has_expected_direction(self):
        rows = backtest.load_dataset()
        valid = {"positive", "negative", "neutral"}
        for r in rows:
            assert r.expected_direction in valid

    def test_each_row_has_strong_alpha(self):
        # Spec required |alpha| >= 0.05 (verified by curation subagent).
        for r in backtest.load_dataset():
            if r.expected_direction == "neutral":
                continue
            assert abs(r.realized_5d_alpha) >= 0.05, (
                f"row {r.ticker}@{r.analysis_date} has weak alpha "
                f"{r.realized_5d_alpha} — not a usable signal"
            )

    def test_diversity_constraints(self):
        # Want at least one negative row and at least three positive rows.
        rows = backtest.load_dataset()
        positives = [r for r in rows if r.expected_direction == "positive"]
        negatives = [r for r in rows if r.expected_direction == "negative"]
        assert len(positives) >= 3
        assert len(negatives) >= 1


@pytest.mark.unit
class TestClassify:
    def test_buy_is_positive(self):
        assert backtest._classify("Buy") == "positive"

    def test_overweight_is_positive(self):
        assert backtest._classify("Overweight") == "positive"

    def test_sell_is_negative(self):
        assert backtest._classify("Sell") == "negative"

    def test_underweight_is_negative(self):
        assert backtest._classify("Underweight") == "negative"

    def test_hold_is_neutral(self):
        assert backtest._classify("Hold") == "neutral"

    def test_failed_is_unknown(self):
        assert backtest._classify("FAILED") == "unknown"


@pytest.mark.unit
class TestMatches:
    def test_exact_positive(self):
        assert backtest._matches("positive", "positive")

    def test_exact_negative(self):
        assert backtest._matches("negative", "negative")

    def test_wrong_direction(self):
        assert not backtest._matches("positive", "negative")
        assert not backtest._matches("negative", "positive")

    def test_neutral_forgiveness(self):
        # neutral row tolerates Hold or unknown but not strong directional calls
        assert backtest._matches("neutral", "neutral")
        assert backtest._matches("unknown", "neutral")
        assert not backtest._matches("positive", "neutral")
        assert not backtest._matches("negative", "neutral")


@pytest.mark.slow
@pytest.mark.verify_directional
def test_backtest_run_passes_threshold():
    """Drive the actual 5-row backtest. ~75 min against Ollama."""
    report = backtest.run_backtest()
    assert report.passed, backtest.report_to_markdown(report)
