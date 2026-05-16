"""Stage-2a: pytest wrappers around the live matrix.

These exercise the real Ollama backend and take a long time per call
(~15 min per deep run). They are ``slow``-marked so default pytest
skips them; run them explicitly with ``-m slow`` or via
``./scripts/verify.ps1`` or the matrix CLI.

Each per-intent test is a tiny wrapper around the matrix verifier so a
failure localises to one intent in your test report rather than a giant
matrix dump.
"""

from __future__ import annotations

import pytest

from tests.verify.matrix import run_matrix
from tests.verify.runner import INTENT_VERIFIERS


# A dedicated output dir for pytest invocations so concurrent runs (CLI
# + pytest) don't share state. Pytest tmpdir would do, but the agent_
# assist orchestrator expects a real path under ~/.tradingagents by
# default; using a tmpdir fixture keeps the test isolated either way.
@pytest.fixture
def verify_output_dir(tmp_path):
    return tmp_path / "agent_assist_verify"


@pytest.mark.slow
@pytest.mark.verify_robustness
@pytest.mark.parametrize("intent", sorted(INTENT_VERIFIERS))
def test_intent_robust_and_shape(intent, verify_output_dir):
    """One real Ollama deep run per intent. Asserts both robustness
    (exit 0 + canonical rating) and shape (Pydantic + markdown headers
    + render round-trip)."""
    verifier = INTENT_VERIFIERS[intent]
    result = verifier(0, output_dir=verify_output_dir)
    # Surface the full PassResult as the failure message so the markdown
    # log isn't the only place to look when a fixture is red.
    msg = (
        f"intent={intent} exit={result.exit_code} ratings={result.ratings} "
        f"error={result.error!r} shape_ok={result.shape_ok}"
    )
    assert result.robust_ok, f"robustness failed: {msg}"
    assert result.shape_ok, f"shape failed: {msg}"


@pytest.mark.slow
@pytest.mark.verify_robustness
def test_full_matrix_one_pass(verify_output_dir):
    """One full pass across every intent. ~2.5-3 hours on Ollama."""
    report = run_matrix(reps=1, output_dir=verify_output_dir)
    assert report.all_green, (
        f"{report.red_count} / {len(report.results)} red — see "
        f"tests/verify/.runs/ for the markdown summary"
    )
