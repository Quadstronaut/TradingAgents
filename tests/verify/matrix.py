"""Top-level verification matrix driver.

Runs one or more passes of the intent verifiers, writes per-pass JSONL
and a human-readable markdown summary, prints a colored board, and
exits non-zero on any red. Supports a 3-pass green-streak loop via
``--until-green N`` for the final acceptance gate.

Usage:
    python -m tests.verify.matrix --reps 1
    python -m tests.verify.matrix --only news_scan
    python -m tests.verify.matrix --until-green 3 --max-attempts 5
    python -m tests.verify.matrix --include-backtest
    python -m tests.verify.matrix --quick   # skip slow deep-run intents (smoke only)
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# Force UTF-8 stdout on Windows. The default code page (cp1252) cannot
# encode the arrow / em-dash glyphs rich.Live emits, and that error fires
# during the progress-display teardown — destroying an otherwise-successful
# 25-minute deep run. See specific-intent failure on 2026-05-15.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        # Detached streams (e.g., pytest capture) don't expose reconfigure;
        # they're already non-cp1252 so this is fine to skip.
        pass

from tests.verify.runner import (
    INTENT_VERIFIERS,
    PassResult,
    pass_result_to_dict,
)

logger = logging.getLogger(__name__)


# Intents that don't need a full deep run — useful for the --quick path.
_QUICK_INTENTS: tuple[str, ...] = ("news_scan",)

# All intents, in the order they should run within a pass.
_ALL_INTENTS: tuple[str, ...] = tuple(INTENT_VERIFIERS)


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------


@dataclass
class MatrixReport:
    """Aggregate result of one or more passes."""

    started_at: str
    finished_at: str
    intents: list[str]
    reps: int
    results: list[PassResult] = field(default_factory=list)

    @property
    def all_green(self) -> bool:
        return all(r.robust_ok and r.shape_ok for r in self.results)

    @property
    def red_count(self) -> int:
        return sum(1 for r in self.results if not (r.robust_ok and r.shape_ok))

    def to_jsonl_lines(self) -> Iterable[str]:
        for r in self.results:
            yield json.dumps(pass_result_to_dict(r))


# ---------------------------------------------------------------------------
# ANSI helpers (rich would be nicer, but stdlib keeps imports lean)
# ---------------------------------------------------------------------------


_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_DIM = "\x1b[90m"
_RESET = "\x1b[0m"


def _supports_color() -> bool:
    return sys.stdout.isatty()


def _c(s: str, color: str) -> str:
    return f"{color}{s}{_RESET}" if _supports_color() else s


# ---------------------------------------------------------------------------
# Pass runner
# ---------------------------------------------------------------------------


def _default_output_dir() -> Path:
    return Path.home() / ".tradingagents" / "agent_assist" / "verify"


def _default_log_dir() -> Path:
    # Repo-local so summaries live next to the test code, not in user-home.
    return Path(__file__).parent / ".runs"


def _now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _ts_slug() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _select_intents(*, only: Optional[str], quick: bool) -> list[str]:
    if only:
        if only not in INTENT_VERIFIERS:
            raise SystemExit(
                f"unknown intent {only!r}; valid: {sorted(INTENT_VERIFIERS)}"
            )
        return [only]
    if quick:
        return list(_QUICK_INTENTS)
    return list(_ALL_INTENTS)


def run_matrix(
    *,
    reps: int = 1,
    seed_start: int = 0,
    only: Optional[str] = None,
    quick: bool = False,
    output_dir: Optional[Path] = None,
    log_dir: Optional[Path] = None,
) -> MatrixReport:
    """Run ``reps`` passes, one row per intent. Returns the aggregated report."""
    output_dir = output_dir or _default_output_dir()
    log_dir = log_dir or _default_log_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    intents = _select_intents(only=only, quick=quick)

    started = _now_iso()
    results: list[PassResult] = []

    for rep in range(reps):
        pass_no = seed_start + rep
        print(_c(f"=== Pass #{pass_no} ({rep + 1}/{reps}) ===", _DIM))
        for intent in intents:
            verifier = INTENT_VERIFIERS[intent]
            print(_c(f"[{pass_no}] {intent:<18} running…", _DIM))
            r = verifier(pass_no, output_dir=output_dir)
            results.append(r)
            _print_row(r)

    finished = _now_iso()
    report = MatrixReport(
        started_at=started, finished_at=finished, intents=intents,
        reps=reps, results=results,
    )

    _write_log(report, log_dir=log_dir)
    _print_board(report)
    return report


def _print_row(r: PassResult) -> None:
    if r.robust_ok and r.shape_ok:
        status = _c("GREEN", _GREEN)
    elif r.robust_ok and not r.shape_ok:
        status = _c("SHAPE", _YELLOW)
    else:
        status = _c("RED  ", _RED)
    ratings = ",".join(r.ratings) if r.ratings else "—"
    err = f" err={r.error.splitlines()[0]}" if r.error else ""
    print(f"  [{r.pass_no}] {r.intent:<18} {status} {r.elapsed_sec:6.1f}s  {ratings}{err}")


def _print_board(report: MatrixReport) -> None:
    print()
    print("=" * 60)
    if report.all_green:
        print(_c("ALL GREEN", _GREEN))
    else:
        print(_c(f"{report.red_count} RED / {len(report.results)} total", _RED))
    print(f"started:  {report.started_at}")
    print(f"finished: {report.finished_at}")
    print("=" * 60)


def _write_log(report: MatrixReport, *, log_dir: Path) -> None:
    slug = _ts_slug()
    jsonl = log_dir / f"{slug}.jsonl"
    md = log_dir / f"{slug}.md"

    with jsonl.open("w", encoding="utf-8") as f:
        for line in report.to_jsonl_lines():
            f.write(line + "\n")

    lines = [
        f"# Verification matrix — {report.started_at}",
        "",
        f"- finished: {report.finished_at}",
        f"- intents: {', '.join(report.intents)}",
        f"- reps: {report.reps}",
        f"- result: {'ALL GREEN' if report.all_green else f'{report.red_count} RED'}",
        "",
        "## Results",
        "",
        "| Pass | Intent | Status | Elapsed | Ratings | Error |",
        "|---|---|---|---|---|---|",
    ]
    for r in report.results:
        if r.robust_ok and r.shape_ok:
            status = "GREEN"
        elif r.robust_ok and not r.shape_ok:
            status = "SHAPE"
        else:
            status = "RED"
        err_short = (r.error.splitlines()[0] if r.error else "").replace("|", "\\|")
        rating_str = ",".join(r.ratings) if r.ratings else "—"
        lines.append(
            f"| {r.pass_no} | {r.intent} | {status} | "
            f"{r.elapsed_sec:.1f}s | {rating_str} | {err_short} |"
        )
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"log: {jsonl}")
    print(f"summary: {md}")


# ---------------------------------------------------------------------------
# Green-streak loop
# ---------------------------------------------------------------------------


def run_until_green(
    *,
    consecutive: int = 3,
    max_attempts: int = 10,
    quick: bool = False,
    only: Optional[str] = None,
    output_dir: Optional[Path] = None,
    log_dir: Optional[Path] = None,
) -> tuple[bool, list[MatrixReport]]:
    """Run the matrix until ``consecutive`` clean passes in a row.

    Returns (success, list-of-reports). ``success`` is False if
    ``max_attempts`` is hit before the streak goal.
    """
    streak = 0
    attempt = 0
    reports: list[MatrixReport] = []
    while streak < consecutive and attempt < max_attempts:
        attempt += 1
        print(_c(f"\n### Attempt {attempt} / streak {streak}/{consecutive} ###", _DIM))
        report = run_matrix(
            reps=1, seed_start=attempt - 1, quick=quick, only=only,
            output_dir=output_dir, log_dir=log_dir,
        )
        reports.append(report)
        if report.all_green:
            streak += 1
            print(_c(f"streak now {streak}/{consecutive}", _GREEN))
        else:
            streak = 0
            print(_c("streak reset", _RED))
    return streak >= consecutive, reports


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reps", type=int, default=1, help="passes per invocation")
    p.add_argument("--seed", type=int, default=0, help="starting pass number")
    p.add_argument(
        "--only", type=str, default=None,
        help=f"run a single intent ({sorted(INTENT_VERIFIERS)})",
    )
    p.add_argument(
        "--quick", action="store_true",
        help="only run intents that skip the full deep pipeline",
    )
    p.add_argument(
        "--until-green", type=int, default=0, metavar="N",
        help="loop until N consecutive clean passes; ignores --reps",
    )
    p.add_argument(
        "--max-attempts", type=int, default=10,
        help="abort the green-streak loop after this many tries",
    )
    args = p.parse_args(argv)

    if args.until_green:
        success, _reports = run_until_green(
            consecutive=args.until_green,
            max_attempts=args.max_attempts,
            quick=args.quick,
            only=args.only,
        )
        return 0 if success else 1

    report = run_matrix(
        reps=args.reps, seed_start=args.seed, only=args.only, quick=args.quick,
    )
    return 0 if report.all_green else 1


if __name__ == "__main__":
    sys.exit(main())
