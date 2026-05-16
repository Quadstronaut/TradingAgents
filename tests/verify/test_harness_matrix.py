"""Unit tests for ``tests.verify.matrix``.

The expensive ``run_matrix`` path is exercised by live runs; these tests
lock in the cheap-but-load-bearing pieces: the intent selector, the
green-streak loop (critical for the 3x acceptance gate), and the
markdown/jsonl writer.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.verify import matrix
from tests.verify.runner import PassResult, ShapeReport


def _green_pass(intent: str = "specific", pass_no: int = 0) -> PassResult:
    return PassResult(
        pass_no=pass_no, intent=intent, inputs={},
        exit_code=0, ratings=["Buy"],
        shapes=[ShapeReport(
            ticker="X",
            final_proposal_present=True,
            recommendation_header_present=True,
            rating_header_present=True,
            pydantic_validated=True,
        )],
        elapsed_sec=0.1,
    )


def _red_pass(intent: str = "specific", pass_no: int = 0) -> PassResult:
    return PassResult(
        pass_no=pass_no, intent=intent, inputs={},
        exit_code=99, ratings=[], shapes=[],
        elapsed_sec=0.1, error="boom",
    )


@pytest.mark.unit
class TestSelectIntents:
    def test_default_returns_all(self):
        out = matrix._select_intents(only=None, quick=False)
        assert set(out) == set(matrix.INTENT_VERIFIERS)

    def test_only_returns_singleton(self):
        out = matrix._select_intents(only="news_scan", quick=False)
        assert out == ["news_scan"]

    def test_only_rejects_unknown(self):
        with pytest.raises(SystemExit):
            matrix._select_intents(only="nope", quick=False)

    def test_quick_only_fast_intents(self):
        out = matrix._select_intents(only=None, quick=True)
        assert "news_scan" in out
        assert "specific" not in out


@pytest.mark.unit
class TestRunMatrix:
    def test_calls_each_verifier_once_per_rep(self, tmp_path):
        called = []
        fake_verifier = lambda pn, *, output_dir: (called.append((pn, "specific")), _green_pass(pass_no=pn))[-1]
        with patch.dict(matrix.INTENT_VERIFIERS, {"specific": fake_verifier}):
            report = matrix.run_matrix(
                reps=2, seed_start=5, only="specific",
                output_dir=tmp_path, log_dir=tmp_path / "runs",
            )
        assert called == [(5, "specific"), (6, "specific")]
        assert report.all_green is True
        assert len(report.results) == 2

    def test_red_run_flips_all_green(self, tmp_path):
        bad_verifier = lambda pn, *, output_dir: _red_pass(pass_no=pn)
        with patch.dict(matrix.INTENT_VERIFIERS, {"specific": bad_verifier}):
            report = matrix.run_matrix(
                reps=1, only="specific",
                output_dir=tmp_path, log_dir=tmp_path / "runs",
            )
        assert report.all_green is False
        assert report.red_count == 1


@pytest.mark.unit
class TestRunUntilGreen:
    def test_three_consecutive_greens_passes(self, tmp_path):
        green_verifier = lambda pn, *, output_dir: _green_pass(pass_no=pn)
        with patch.dict(matrix.INTENT_VERIFIERS, {"specific": green_verifier}):
            success, reports = matrix.run_until_green(
                consecutive=3, max_attempts=5, only="specific",
                output_dir=tmp_path, log_dir=tmp_path / "runs",
            )
        assert success is True
        assert len(reports) == 3

    def test_red_at_attempt_2_resets_streak(self, tmp_path):
        # green, green, RED, green, green, green => 6 attempts, 3-green run starts at 4.
        sequence = [_green_pass(), _green_pass(), _red_pass(),
                    _green_pass(), _green_pass(), _green_pass()]
        cursor = {"i": 0}

        def picky_verifier(pn, *, output_dir):
            r = sequence[cursor["i"]]
            cursor["i"] += 1
            return r

        with patch.dict(matrix.INTENT_VERIFIERS, {"specific": picky_verifier}):
            success, reports = matrix.run_until_green(
                consecutive=3, max_attempts=10, only="specific",
                output_dir=tmp_path, log_dir=tmp_path / "runs",
            )
        assert success is True
        assert len(reports) == 6
        # Confirm the red one is in the middle.
        assert reports[2].all_green is False
        assert all(r.all_green for r in reports[3:])

    def test_max_attempts_exhausted(self, tmp_path):
        bad_verifier = lambda pn, *, output_dir: _red_pass(pass_no=pn)
        with patch.dict(matrix.INTENT_VERIFIERS, {"specific": bad_verifier}):
            success, reports = matrix.run_until_green(
                consecutive=3, max_attempts=4, only="specific",
                output_dir=tmp_path, log_dir=tmp_path / "runs",
            )
        assert success is False
        assert len(reports) == 4
        assert all(not r.all_green for r in reports)


@pytest.mark.unit
class TestLogWriter:
    def test_jsonl_and_md_files_created(self, tmp_path):
        report = matrix.MatrixReport(
            started_at="2026-01-01T00:00:00",
            finished_at="2026-01-01T00:00:01",
            intents=["x"], reps=1,
            results=[_green_pass(intent="x")],
        )
        matrix._write_log(report, log_dir=tmp_path)
        files = list(tmp_path.iterdir())
        assert any(p.suffix == ".jsonl" for p in files)
        assert any(p.suffix == ".md" for p in files)

    def test_jsonl_line_count_matches_results(self, tmp_path):
        report = matrix.MatrixReport(
            started_at="t0", finished_at="t1",
            intents=["x"], reps=3,
            results=[_green_pass(), _red_pass(), _green_pass()],
        )
        matrix._write_log(report, log_dir=tmp_path)
        jsonl = next(p for p in tmp_path.iterdir() if p.suffix == ".jsonl")
        lines = jsonl.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
