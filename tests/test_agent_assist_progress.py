"""Tests for the progress display state machine.

These exercise ProgressState directly (no Live, no key listener).
Rendering integrity is checked by asking the renderable to render once.
"""

from __future__ import annotations

import time

import pytest

from tradingagents.agent_assist import progress as p


def _state() -> p.ProgressState:
    return p.ProgressState(
        ticker="NVDA",
        label="full deep analysis",
        phases=p.full_pipeline_phases(),
        estimated_total_sec=900,
    )


@pytest.mark.unit
def test_first_node_event_starts_phase_zero():
    s = _state()
    s.on_node_event("Market Analyst", {})
    assert s.phases[0].is_active
    assert s.phases[1].started_at is None


@pytest.mark.unit
def test_node_in_later_phase_marks_earlier_phases_done():
    s = _state()
    s.on_node_event("Market Analyst", {})
    # Jump straight to Bull Researcher (phase index 4)
    s.on_node_event("Bull Researcher", {})
    assert s.phases[0].is_done
    assert s.phases[1].is_done
    assert s.phases[2].is_done
    assert s.phases[3].is_done
    assert s.phases[4].is_active


@pytest.mark.unit
def test_unknown_node_name_is_ignored():
    s = _state()
    s.on_node_event("Definitely Not A Real Node", {})
    assert all(not ph.is_active for ph in s.phases)


@pytest.mark.unit
def test_finish_completes_remaining_phases():
    s = _state()
    s.on_node_event("Portfolio Manager", {})
    s.finish()
    assert all(ph.is_done for ph in s.phases)
    assert s.finished


@pytest.mark.unit
def test_view_toggle_swaps_overview_and_detail():
    s = _state()
    assert s.view_mode == "overview"
    s.toggle_view()
    assert s.view_mode == "detail"
    s.toggle_view()
    assert s.view_mode == "overview"


@pytest.mark.unit
def test_quiet_forces_overview_and_disables_toggle():
    s = _state()
    s.view_mode = "detail"
    s.quiet()
    assert s.view_mode == "overview"
    s.toggle_view()  # should be a no-op
    assert s.view_mode == "overview"


@pytest.mark.unit
def test_detail_buffer_captures_messages_and_tool_calls():
    s = _state()

    class FakeMsg:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []

    s.on_node_event("Market Analyst", {
        "messages": [
            FakeMsg(content="NVDA closed at $219.44, up 2.1%."),
            FakeMsg(tool_calls=[{"name": "get_indicators", "args": {"ticker": "NVDA"}}]),
        ],
    })
    assert any(e.kind == "msg" and "NVDA closed" in e.content for e in s.detail_buffer)
    assert any(e.kind == "tool" and "get_indicators" in e.content for e in s.detail_buffer)


@pytest.mark.unit
def test_renderable_handles_empty_state_without_error():
    s = _state()
    r = p._Renderable(s)
    # __rich__ should produce a renderable, not raise.
    out = r.__rich__()
    assert out is not None


@pytest.mark.unit
def test_renderable_detail_view_with_buffer_renders():
    s = _state()
    s.view_mode = "detail"
    s.on_node_event("Market Analyst", {})
    r = p._Renderable(s)
    out = r.__rich__()
    assert out is not None
