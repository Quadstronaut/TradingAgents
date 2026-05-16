"""Regression: ``_run_one_deep`` must preserve a successful run when the
progress-display teardown raises.

Background — 2026-05-15 specific-intent verification:
  After 25.5 min of successful work (ratings extracted, markdown rendered)
  rich.Live tried to write its final buffer through Windows cp1252 stdout.
  The ↑ glyph in the legend triggered UnicodeEncodeError. The exception
  propagated out of the ``with progress_display(...)`` block and was
  caught by the outer ``except Exception``, returning rating="FAILED".

The orchestrator now captures the RunResult INSIDE the ``with`` and
returns it from a defensive branch when the post-yield teardown raises.
This test pins that behaviour.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agent_assist import orchestrator
from tradingagents.agent_assist.summarize import RunResult


def _fake_graph_with_rating(rating: str, decision_md: str):
    fake = MagicMock()
    fake.propagate.return_value = (
        {"final_trade_decision": decision_md},
        rating,
    )
    return fake


@pytest.mark.unit
class TestTeardownResilience:
    """progress_display teardown exception must not destroy a successful run."""

    @contextmanager
    def _exploding_progress(self, ps_obj):
        """Yields ps_obj, then raises UnicodeEncodeError on exit."""
        yield ps_obj
        # mimic rich.Live's __exit__ teardown
        raise UnicodeEncodeError(
            "charmap", "↑", 0, 1,
            "character maps to <undefined>",
        )

    def test_successful_run_survives_teardown_unicode_error(self):
        fake_graph = _fake_graph_with_rating(
            "Buy",
            "**Recommendation**: Buy\nFINAL TRANSACTION PROPOSAL: **BUY**",
        )
        ps_obj = MagicMock()
        # Returns the context manager when called with the state arg.
        progress_cm = lambda state, **_: self._exploding_progress(ps_obj)

        with patch.object(
            orchestrator, "TradingAgentsGraph", return_value=fake_graph,
        ), patch.object(orchestrator, "progress_display", progress_cm):
            result = orchestrator._run_one_deep("NVDA", today="2026-05-15")

        # Pre-fix this would be ("FAILED", "UnicodeEncodeError: ...").
        assert result.rating == "Buy"
        assert result.error is None
        assert "FINAL TRANSACTION PROPOSAL" in (result.decision_md or "")

    def test_failure_inside_propagate_still_returns_failed(self):
        """A real propagate exception (not teardown) still surfaces as FAILED."""
        fake_graph = MagicMock()
        fake_graph.propagate.side_effect = RuntimeError("ollama unreachable")
        ps_obj = MagicMock()

        @contextmanager
        def clean_progress(state, **_):
            yield ps_obj

        with patch.object(
            orchestrator, "TradingAgentsGraph", return_value=fake_graph,
        ), patch.object(orchestrator, "progress_display", clean_progress):
            result = orchestrator._run_one_deep("NVDA", today="2026-05-15")

        assert result.rating == "FAILED"
        assert "ollama unreachable" in (result.error or "")

    def test_keyboard_interrupt_propagates(self):
        fake_graph = MagicMock()
        fake_graph.propagate.side_effect = KeyboardInterrupt()
        ps_obj = MagicMock()

        @contextmanager
        def clean_progress(state, **_):
            yield ps_obj

        with patch.object(
            orchestrator, "TradingAgentsGraph", return_value=fake_graph,
        ), patch.object(orchestrator, "progress_display", clean_progress):
            with pytest.raises(KeyboardInterrupt):
                orchestrator._run_one_deep("NVDA", today="2026-05-15")
