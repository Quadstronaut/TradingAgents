"""Directional-sanity backtest.

For each row in ``backtest_dataset.csv``, run the deep pipeline with the
analysis_date as ``today``, then check whether the resulting rating's
direction matches the realised 5-day alpha direction.

A direction map keeps the test forgiving where it should be (Buy and
Overweight both count as 'positive') while still being strict where it
matters ('Buy' on a -13% alpha row is a clear miss).

Pass condition is ``>=4 of 5`` rows match. LLMs are not oracles; we
expect occasional misses on otherwise-correct signal.
"""

from __future__ import annotations

import csv
import datetime
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from tradingagents.agent_assist import orchestrator as _orch
from tradingagents.agent_assist.summarize import RunResult

logger = logging.getLogger(__name__)

DATASET_PATH = Path(__file__).parent / "fixtures" / "backtest_dataset.csv"


# How tolerant to be: per-row directions and the aggregate pass threshold.
_RATING_TO_DIRECTION: dict[str, str] = {
    "Buy": "positive",
    "Overweight": "positive",
    "Hold": "neutral",
    "Underweight": "negative",
    "Sell": "negative",
}

_MIN_MATCHES_REQUIRED = 4   # of len(rows); spec says >= 4 of 5


@dataclass(frozen=True)
class BacktestRow:
    ticker: str
    analysis_date: str
    realized_5d_alpha: float
    expected_direction: str   # "positive" / "negative" / "neutral"


@dataclass
class BacktestRunResult:
    row: BacktestRow
    rating: str
    direction: str
    matched: bool
    elapsed_sec: float
    error: Optional[str] = None
    decision_md: Optional[str] = None


@dataclass
class BacktestReport:
    rows: list[BacktestRow]
    runs: list[BacktestRunResult] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return sum(1 for r in self.runs if r.matched)

    @property
    def passed(self) -> bool:
        return self.matched_count >= _MIN_MATCHES_REQUIRED


def load_dataset(path: Path = DATASET_PATH) -> list[BacktestRow]:
    """Read ``backtest_dataset.csv`` and yield typed rows."""
    rows: list[BacktestRow] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            rows.append(BacktestRow(
                ticker=raw["ticker"].strip(),
                analysis_date=raw["analysis_date"].strip(),
                realized_5d_alpha=float(raw["realized_5d_alpha"]),
                expected_direction=raw["expected_direction"].strip().lower(),
            ))
    if not rows:
        raise RuntimeError(f"empty backtest dataset at {path!s}")
    return rows


def _classify(rating: str) -> str:
    return _RATING_TO_DIRECTION.get(rating, "unknown")


def _matches(actual: str, expected: str) -> bool:
    """Be slightly forgiving on the 'neutral' axis.

    If the row's realised alpha is between ±2% (already encoded as
    'neutral' in the dataset), then Hold or any unknown direction is
    not a miss. For positive/negative rows, only an exact direction
    match counts.
    """
    if expected == "neutral":
        return actual in ("neutral", "unknown")
    return actual == expected


def run_one_row(row: BacktestRow, *, output_dir: Optional[Path] = None) -> BacktestRunResult:
    """Run the deep pipeline at the historical analysis_date for one row."""
    start = time.monotonic()
    try:
        result: RunResult = _orch._run_one_deep(
            row.ticker,
            today=row.analysis_date,
            label=f"backtest {row.ticker} {row.analysis_date}",
        )
        rating = result.rating
        direction = _classify(rating)
        matched = (
            rating != "FAILED"
            and _matches(direction, row.expected_direction)
        )
        return BacktestRunResult(
            row=row,
            rating=rating,
            direction=direction,
            matched=matched,
            elapsed_sec=time.monotonic() - start,
            error=result.error,
            decision_md=result.decision_md,
        )
    except Exception as exc:
        logger.exception("backtest row failed for %s @ %s", row.ticker, row.analysis_date)
        return BacktestRunResult(
            row=row,
            rating="FAILED",
            direction="unknown",
            matched=False,
            elapsed_sec=time.monotonic() - start,
            error=f"{type(exc).__name__}: {exc}",
        )


def run_backtest(
    *,
    output_dir: Optional[Path] = None,
    rows: Optional[list[BacktestRow]] = None,
) -> BacktestReport:
    """Run every backtest row and return the aggregate report."""
    rows = rows or load_dataset()
    report = BacktestReport(rows=rows)
    for row in rows:
        run = run_one_row(row, output_dir=output_dir)
        report.runs.append(run)
        print(
            f"[backtest] {row.ticker}@{row.analysis_date}: "
            f"rating={run.rating} dir={run.direction} "
            f"expected={row.expected_direction} "
            f"{'OK' if run.matched else 'MISS'} ({run.elapsed_sec:.0f}s)"
        )
    return report


def report_to_markdown(report: BacktestReport) -> str:
    lines = [
        "# Directional-sanity backtest",
        "",
        f"- rows: {len(report.rows)}",
        f"- matched: {report.matched_count}/{len(report.rows)} "
        f"(min required {_MIN_MATCHES_REQUIRED})",
        f"- passed: **{report.passed}**",
        "",
        "| Ticker | Date | Alpha | Expected | Rating | Direction | Match | Elapsed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report.runs:
        lines.append(
            f"| {r.row.ticker} | {r.row.analysis_date} | "
            f"{r.row.realized_5d_alpha:+.3f} | {r.row.expected_direction} | "
            f"{r.rating} | {r.direction} | "
            f"{'yes' if r.matched else 'no'} | {r.elapsed_sec:.0f}s |"
        )
    return "\n".join(lines)
