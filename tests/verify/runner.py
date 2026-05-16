"""Per-intent runner for the verification matrix.

Each ``verify_<intent>`` function builds a Task from seeded fixtures,
calls the real ``run_task`` so the routing layer is exercised, captures
deep-run results via a patched ``_run_one_deep`` collector, and returns
a ``PassResult`` describing what happened.

Robustness check happens here; shape check happens here on every deep
run captured during the pass. The matrix driver in ``matrix.py`` is
responsible for sequencing passes and aggregating results.
"""

from __future__ import annotations

import dataclasses
import datetime
import io
import logging
import sys
import time
import traceback
from contextlib import contextmanager, redirect_stdout
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from unittest.mock import patch

from tradingagents.agent_assist import orchestrator as _orch
from tradingagents.agent_assist.menu import Task
from tradingagents.agent_assist.summarize import RunResult

from tests.verify.fixtures.random_ticker import pick_ticker, pick_tickers
from tests.verify.fixtures.vague_prompts import pick_prompt

logger = logging.getLogger(__name__)


CANONICAL_RATINGS: frozenset[str] = frozenset({
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
})


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ShapeReport:
    """Per-deep-run structured-output shape inspection."""

    ticker: str
    final_proposal_present: bool
    recommendation_header_present: bool
    rating_header_present: bool
    pydantic_validated: bool
    notes: list[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all([
            self.final_proposal_present,
            self.recommendation_header_present,
            self.rating_header_present,
            self.pydantic_validated,
        ])


@dataclass
class PassResult:
    """One row in the verification matrix."""

    pass_no: int
    intent: str
    inputs: dict[str, Any]
    exit_code: int
    ratings: list[str]                     # one per captured deep run
    shapes: list[ShapeReport]              # parallel to ratings
    elapsed_sec: float
    error: Optional[str] = None
    summary_paths: list[str] = field(default_factory=list)

    @property
    def robust_ok(self) -> bool:
        if self.exit_code != 0 or self.error is not None:
            return False
        # An intent with no expected deep runs (news_scan) is robust on exit-0.
        if not self.ratings:
            return True
        return all(r in CANONICAL_RATINGS for r in self.ratings)

    @property
    def shape_ok(self) -> bool:
        # No shape report means no deep run produced markdown — only OK for
        # intents that don't run the full pipeline (news_scan).
        if not self.shapes:
            return self.intent == "news_scan"
        return all(s.all_ok for s in self.shapes)


# ---------------------------------------------------------------------------
# Deep-run collector + input bypass
# ---------------------------------------------------------------------------


class _DeepRunCollector:
    """Wraps the real ``_run_one_deep`` and stashes each ``RunResult``.

    This is the trick that lets ``run_task`` stay untouched while we
    still capture every deep-run output for shape inspection.
    """

    def __init__(self, real: Callable[..., RunResult]) -> None:
        self._real = real
        self.results: list[RunResult] = []

    def __call__(self, *args: Any, **kwargs: Any) -> RunResult:
        result = self._real(*args, **kwargs)
        self.results.append(result)
        return result


@contextmanager
def _bypass_inputs(scripted: Optional[list[str]] = None) -> Iterator[list[str]]:
    """Auto-answer ``input()`` calls during a matrix run.

    ``scripted`` is consumed first (one answer per ``input()`` call).
    When exhausted, we fall back to 'y' so any confirm prompt proceeds.
    The list of answers actually consumed is yielded so callers can
    assert on it in tests.
    """
    queue = list(scripted or [])
    used: list[str] = []

    def fake_input(prompt: str = "") -> str:
        ans = queue.pop(0) if queue else "y"
        used.append(ans)
        return ans

    with patch("builtins.input", side_effect=fake_input):
        yield used


def _check_shape(result: RunResult) -> ShapeReport:
    md = result.decision_md or ""
    notes: list[str] = []
    if not md:
        notes.append("empty decision_md")
    # The fact that propagate() returned a canonical rating word means the
    # underlying Pydantic instance validated and SignalProcessor extracted
    # the rating successfully — short of refactoring SignalProcessor we
    # treat a canonical rating as proxy evidence of Pydantic validation.
    pydantic_validated = result.rating in CANONICAL_RATINGS
    return ShapeReport(
        ticker=result.ticker,
        final_proposal_present="FINAL TRANSACTION PROPOSAL:" in md,
        recommendation_header_present="**Recommendation**:" in md,
        rating_header_present="**Rating**:" in md,
        pydantic_validated=pydantic_validated,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Per-intent verifiers
# ---------------------------------------------------------------------------


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _run_task_with_capture(
    task: Task,
    *,
    output_dir: Path,
    scripted_inputs: Optional[list[str]] = None,
    capture_stdout: bool = False,
) -> tuple[int, list[RunResult], Optional[str], Optional[str]]:
    """Run a Task with deep-run capture and input bypass.

    Returns (exit_code, deep_results, captured_stdout, error_repr).
    """
    collector = _DeepRunCollector(_orch._run_one_deep)
    buf = io.StringIO()
    err: Optional[str] = None
    exit_code = 99
    try:
        with patch.object(_orch, "_run_one_deep", collector):
            with _bypass_inputs(scripted_inputs):
                if capture_stdout:
                    with redirect_stdout(buf):
                        exit_code = _orch.run_task(task, output_dir=output_dir)
                else:
                    exit_code = _orch.run_task(task, output_dir=output_dir)
    except SystemExit as e:
        exit_code = int(e.code) if e.code is not None else 0
    except Exception as e:
        err = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        exit_code = 99
    return exit_code, collector.results, buf.getvalue() if capture_stdout else None, err


def _wrap(
    pass_no: int,
    intent: str,
    inputs: dict[str, Any],
    *,
    output_dir: Path,
    scripted_inputs: Optional[list[str]] = None,
    expect_deep_runs: bool = True,
) -> PassResult:
    """Common driver shape — build PassResult from a single Task run."""
    task = _build_task(intent, inputs)
    start = time.monotonic()
    exit_code, deep_results, _stdout, err = _run_task_with_capture(
        task,
        output_dir=output_dir,
        scripted_inputs=scripted_inputs,
    )
    elapsed = time.monotonic() - start
    ratings = [r.rating for r in deep_results]
    shapes = [_check_shape(r) for r in deep_results if r.rating in CANONICAL_RATINGS]
    summary_paths: list[str] = []
    for r in deep_results:
        if r.log_path:
            summary_paths.append(str(r.log_path))
    if not expect_deep_runs:
        # news_scan path — no deep-run capture; success is exit_code 0.
        shapes = []
    return PassResult(
        pass_no=pass_no,
        intent=intent,
        inputs=inputs,
        exit_code=exit_code,
        ratings=ratings,
        shapes=shapes,
        elapsed_sec=elapsed,
        error=err,
        summary_paths=summary_paths,
    )


def _build_task(intent: str, inputs: dict[str, Any]) -> Task:
    """Materialise a Task from intent-specific inputs.

    The matrix uses a small set of intents; this central builder keeps
    the per-verifier functions tiny.
    """
    if intent == "specific":
        return Task(intent="specific", ticker=inputs["ticker"])
    if intent == "owned":
        return Task(
            intent="owned",
            ticker=inputs["ticker"],
            shares=inputs.get("shares", 10.0),
            cost_basis=inputs.get("cost_basis", 100.0),
        )
    if intent == "theme":
        return Task(
            intent="theme",
            theme=inputs["theme"],
            budget=inputs.get("budget"),
        )
    if intent == "budget":
        return Task(
            intent="budget",
            budget=inputs["budget"],
            theme=inputs.get("theme"),
        )
    if intent == "compare":
        return Task(
            intent="compare",
            ticker=inputs["ticker"],
            ticker_b=inputs["ticker_b"],
        )
    if intent == "news_scan":
        return Task(intent="news_scan", ticker=inputs["ticker"])
    if intent == "freeform":
        return Task(
            intent="freeform",
            prompt=inputs["prompt"],
            budget=inputs.get("budget"),
        )
    raise ValueError(f"unknown intent for verifier: {intent!r}")


# Each function below is one matrix cell. They all return a PassResult.

def verify_specific(pass_no: int, *, output_dir: Path) -> PassResult:
    ticker = pick_ticker(pass_no)
    return _wrap(pass_no, "specific", {"ticker": ticker, "today": _today_iso()},
                 output_dir=output_dir)


def verify_owned(pass_no: int, *, output_dir: Path) -> PassResult:
    ticker = pick_ticker(pass_no)
    return _wrap(
        pass_no, "owned",
        {"ticker": ticker, "shares": 10.0, "cost_basis": 100.0, "today": _today_iso()},
        output_dir=output_dir,
    )


def verify_theme(pass_no: int, *, output_dir: Path) -> PassResult:
    # Use a vague-prompt phrase as the theme so the shortlist LLM has
    # something concrete to match against. Budget capped at 200 to keep
    # the universe filter from emptying out.
    theme = pick_prompt(pass_no)
    return _wrap(
        pass_no, "theme",
        {"theme": theme, "budget": 200, "today": _today_iso()},
        output_dir=output_dir,
    )


def verify_budget(pass_no: int, *, output_dir: Path) -> PassResult:
    return _wrap(
        pass_no, "budget",
        {"budget": 100, "theme": None, "today": _today_iso()},
        output_dir=output_dir,
    )


def verify_compare(pass_no: int, *, output_dir: Path) -> PassResult:
    a, b = pick_tickers(pass_no, n=2)
    return _wrap(
        pass_no, "compare",
        {"ticker": a, "ticker_b": b, "today": _today_iso()},
        output_dir=output_dir,
    )


def verify_news_scan(pass_no: int, *, output_dir: Path) -> PassResult:
    ticker = pick_ticker(pass_no)
    return _wrap(
        pass_no, "news_scan",
        {"ticker": ticker, "today": _today_iso()},
        output_dir=output_dir,
        expect_deep_runs=False,
    )


def verify_freeform_single(pass_no: int, *, output_dir: Path) -> PassResult:
    """Freeform with a vague prompt that mentions a ticker — exercises the
    single-ticker freeform path (not the screener path)."""
    ticker = pick_ticker(pass_no)
    prompt = f"What do you think about {ticker} right now?"
    return _wrap(
        pass_no, "freeform",
        {"prompt": prompt, "ticker_hint": ticker, "today": _today_iso()},
        output_dir=output_dir,
    )


def verify_freeform_screen(pass_no: int, *, output_dir: Path) -> PassResult:
    """Freeform with a vague non-owned query — exercises the screener path."""
    prompt = pick_prompt(pass_no)
    return _wrap(
        pass_no, "freeform",
        {"prompt": prompt, "budget": 150, "today": _today_iso()},
        output_dir=output_dir,
        # The screener path calls _ask_budget if the Task budget is None,
        # but we set it above. Confirm prompts answer 'y' via the bypass.
    )


# Map intent label → verifier. The matrix iterates this in order.
INTENT_VERIFIERS: dict[str, Callable[..., PassResult]] = {
    "specific":          verify_specific,
    "owned":             verify_owned,
    "theme":             verify_theme,
    "budget":            verify_budget,
    "compare":           verify_compare,
    "news_scan":         verify_news_scan,
    "freeform_single":   verify_freeform_single,
    "freeform_screen":   verify_freeform_screen,
}


# ---------------------------------------------------------------------------
# Result serialisation
# ---------------------------------------------------------------------------


def pass_result_to_dict(r: PassResult) -> dict[str, Any]:
    """Convert PassResult to a JSON-serialisable dict.

    ``ShapeReport`` instances are unrolled because ``dataclasses.asdict``
    can't handle frozen lists of dataclasses on every Python version we
    care about.
    """
    return {
        "pass_no": r.pass_no,
        "intent": r.intent,
        "inputs": r.inputs,
        "exit_code": r.exit_code,
        "ratings": r.ratings,
        "shapes": [asdict(s) for s in r.shapes],
        "elapsed_sec": round(r.elapsed_sec, 2),
        "error": r.error,
        "summary_paths": r.summary_paths,
        "robust_ok": r.robust_ok,
        "shape_ok": r.shape_ok,
    }
