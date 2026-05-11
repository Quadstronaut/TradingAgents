"""Live two-pane terminal progress display for agent-assist runs.

Driven by `graph.stream(stream_mode="updates")` events the orchestrator
forwards via ``ProgressState.on_node_event``. Rich's ``Live`` re-renders
the state on a 4 Hz tick so spinners animate even when no events fire.

Views:
    - "overview" (default): phase checklist with per-phase elapsed time
    - "detail": current phase header + recent model messages and tool calls

Toggling:
    - up arrow / Tab → detail
    - down arrow     → overview
    - q              → quiet (force overview, no further toggling)

The key listener runs in a daemon thread and degrades gracefully when
the platform doesn't expose a non-blocking stdin (e.g. inside CI). On
Windows it uses ``msvcrt``; on POSIX it falls back to ``termios``.

The whole module is best-effort: any exception in rendering or key
handling must not interrupt the LangGraph stream that's doing the
actual analysis. Caller wraps the run in ``with ProgressDisplay(...)``.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase model
# ---------------------------------------------------------------------------


@dataclass
class Phase:
    """One displayed phase. May cover multiple LangGraph nodes (analyst loops)."""
    name: str
    node_names: tuple[str, ...]
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    @property
    def is_done(self) -> bool:
        return self.completed_at is not None

    @property
    def is_active(self) -> bool:
        return self.started_at is not None and self.completed_at is None

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return (self.completed_at or time.monotonic()) - self.started_at


def full_pipeline_phases() -> list[Phase]:
    """Phases for the full TradingAgentsGraph deep pipeline."""
    return [
        Phase("Market Analyst",       ("Market Analyst", "tools_market", "Msg Clear Market")),
        Phase("Social Analyst",       ("Social Analyst", "tools_social", "Msg Clear Social")),
        Phase("News Analyst",         ("News Analyst", "tools_news", "Msg Clear News")),
        Phase("Fundamentals Analyst", ("Fundamentals Analyst", "tools_fundamentals", "Msg Clear Fundamentals")),
        Phase("Bull vs Bear debate",  ("Bull Researcher", "Bear Researcher")),
        Phase("Research Manager",     ("Research Manager",)),
        Phase("Trader",               ("Trader",)),
        Phase("Risk debate",          ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst")),
        Phase("Portfolio Manager",    ("Portfolio Manager",)),
    ]


def news_scan_phases() -> list[Phase]:
    """Phases for the lightweight news-scan flow."""
    return [
        Phase("Social Analyst", ("Social Analyst", "tools_social", "Msg Clear Social")),
        Phase("News Analyst",   ("News Analyst", "tools_news", "Msg Clear News")),
        Phase("Verdict",        ("Verdict",)),
    ]


def _node_to_phase(phases: list[Phase]) -> dict[str, int]:
    out: dict[str, int] = {}
    for idx, ph in enumerate(phases):
        for n in ph.node_names:
            out[n] = idx
    return out


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class DetailEntry:
    """One row of the detail pane."""
    kind: str       # "msg" | "tool"
    phase: str
    content: str
    ts: float = field(default_factory=time.monotonic)


@dataclass
class ProgressState:
    ticker: str
    label: str
    phases: list[Phase]
    estimated_total_sec: float = 0.0
    started_at: float = field(default_factory=time.monotonic)
    view_mode: str = "overview"
    quieted: bool = False
    finished: bool = False
    detail_buffer: list[DetailEntry] = field(default_factory=list)
    _node_idx: dict[str, int] = field(init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._node_idx = _node_to_phase(self.phases)

    # ------- event ingestion --------------------------------------------

    def on_node_event(self, node_name: str, payload: Any = None) -> None:
        """Forward one stream-updates chunk into the state."""
        with self._lock:
            idx = self._node_idx.get(node_name)
            if idx is None:
                return
            now = time.monotonic()
            ph = self.phases[idx]
            if ph.started_at is None:
                ph.started_at = now
            # Any node firing in phase j > i means phase i finished.
            for prev in self.phases[:idx]:
                if not prev.is_done:
                    if prev.started_at is None:
                        prev.started_at = now
                    prev.completed_at = now
            self._extract_detail(ph.name, payload)

    def finish(self) -> None:
        """Called by orchestrator after propagate returns. Marks final phase done."""
        with self._lock:
            now = time.monotonic()
            for ph in self.phases:
                if not ph.is_done:
                    if ph.started_at is None:
                        ph.started_at = now
                    ph.completed_at = now
            self.finished = True

    def toggle_view(self) -> None:
        if self.quieted:
            return
        self.view_mode = "detail" if self.view_mode == "overview" else "overview"

    def set_view(self, mode: str) -> None:
        if self.quieted:
            self.view_mode = "overview"
            return
        if mode in ("overview", "detail"):
            self.view_mode = mode

    def quiet(self) -> None:
        self.quieted = True
        self.view_mode = "overview"

    # ------- internals ---------------------------------------------------

    def _extract_detail(self, phase_name: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        messages = payload.get("messages") or []
        for m in messages:
            tool_calls = getattr(m, "tool_calls", None) or []
            content = getattr(m, "content", None)
            for tc in tool_calls:
                if isinstance(tc, dict):
                    name = tc.get("name", "tool")
                    args = tc.get("args", {})
                else:
                    name = getattr(tc, "name", "tool")
                    args = getattr(tc, "args", {})
                self.detail_buffer.append(DetailEntry(
                    kind="tool", phase=phase_name, content=f"{name}({_short_repr(args)})",
                ))
            if content and not tool_calls:
                self.detail_buffer.append(DetailEntry(
                    kind="msg", phase=phase_name, content=str(content),
                ))
        # Bound buffer (keep last 250)
        if len(self.detail_buffer) > 250:
            del self.detail_buffer[:-250]


def _short_repr(value: Any, max_len: int = 80) -> str:
    s = str(value)
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def _fmt_duration(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


_SPINNER = Spinner("dots", text="")


class _Renderable:
    """Bound to Live; ``__rich__`` is called on every tick."""

    def __init__(self, state: ProgressState):
        self.state = state

    def __rich__(self) -> Any:
        try:
            if self.state.view_mode == "detail" and not self.state.quieted:
                return self._render_detail()
            return self._render_overview()
        except Exception:
            logger.exception("progress render failed")
            return Text("(progress render error — analysis continues)")

    def _header(self) -> Text:
        elapsed = time.monotonic() - self.state.started_at
        est_total = self.state.estimated_total_sec
        remaining = max(est_total - elapsed, 0) if est_total else 0
        hint = (
            "  ↑/Tab detail · ↓ overview · q quiet"
            if not self.state.quieted else "  (quieted)"
        )
        text = Text()
        text.append(f"{self.state.ticker} — {self.state.label}\n", style="bold")
        text.append(
            f"elapsed {_fmt_duration(elapsed)}"
            + (f"  ·  est. remaining ~{_fmt_duration(remaining)}" if est_total else "")
            + hint,
            style="dim",
        )
        return text

    def _render_overview(self) -> Any:
        table = Table.grid(padding=(0, 1))
        table.add_column(width=3)   # status
        table.add_column()          # name
        table.add_column(justify="right")  # elapsed
        for ph in self.state.phases:
            if ph.is_done:
                marker: Any = Text("[✓]", style="green")
                elapsed_str = Text(f"({_fmt_duration(ph.elapsed)})", style="green dim")
            elif ph.is_active:
                marker = _SPINNER
                elapsed_str = Text(f"({_fmt_duration(ph.elapsed)}…)", style="yellow")
            else:
                marker = Text("[ ]", style="dim")
                elapsed_str = Text("")
            name = Text(ph.name, style="bold" if ph.is_active else "")
            table.add_row(marker, name, elapsed_str)

        return Panel(
            Group(self._header(), Text(""), table),
            border_style="cyan",
            title="overview",
            title_align="left",
        )

    def _render_detail(self) -> Any:
        # Show entries from the active phase first, falling back to most recent.
        active_phase = next((p for p in self.state.phases if p.is_active), None)
        phase_name = active_phase.name if active_phase else "(idle)"

        recent = self.state.detail_buffer[-20:]
        body = Text()
        for entry in recent:
            if entry.kind == "tool":
                body.append("  · tool: ", style="magenta")
                body.append(f"{entry.content}\n")
            else:
                body.append(f"{entry.content}\n")
        if not recent:
            body.append("(no model output yet — analyst is thinking)", style="dim")

        return Panel(
            Group(
                self._header(),
                Text(""),
                Text(f"── {phase_name} ─" + "─" * 50, style="dim"),
                body,
            ),
            border_style="cyan",
            title="detail",
            title_align="left",
        )


# ---------------------------------------------------------------------------
# Key listener
# ---------------------------------------------------------------------------


class _KeyListener(threading.Thread):
    """Background thread that reads single keystrokes and mutates state.

    Best-effort: if reading keys raises, we log and exit. The display
    still renders; the user just can't toggle.
    """

    def __init__(self, state: ProgressState):
        super().__init__(daemon=True, name="agent-assist-keylistener")
        self.state = state
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            if sys.platform.startswith("win"):
                self._run_windows()
            else:
                self._run_posix()
        except Exception:
            logger.debug("key listener exiting", exc_info=True)

    def _run_windows(self) -> None:
        import msvcrt  # type: ignore[import-not-found]
        while not self._stop.is_set():
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key in (b"\xe0", b"\x00"):  # special-key prefix
                    sub = msvcrt.getch()
                    if sub == b"H":      # up arrow
                        self.state.set_view("detail")
                    elif sub == b"P":    # down arrow
                        self.state.set_view("overview")
                elif key == b"\t":
                    self.state.toggle_view()
                elif key in (b"q", b"Q"):
                    self.state.quiet()
            else:
                time.sleep(0.05)

    def _run_posix(self) -> None:
        try:
            import select
            import termios
            import tty
        except ImportError:
            return
        fd = sys.stdin.fileno()
        try:
            old_settings = termios.tcgetattr(fd)
        except (termios.error, OSError):
            return  # not a tty
        try:
            tty.setcbreak(fd)
            while not self._stop.is_set():
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if not r:
                    continue
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    ch2 = sys.stdin.read(1) if select.select([sys.stdin], [], [], 0.01)[0] else ""
                    ch3 = sys.stdin.read(1) if select.select([sys.stdin], [], [], 0.01)[0] else ""
                    if ch2 == "[" and ch3 == "A":
                        self.state.set_view("detail")
                    elif ch2 == "[" and ch3 == "B":
                        self.state.set_view("overview")
                elif ch == "\t":
                    self.state.toggle_view()
                elif ch in ("q", "Q"):
                    self.state.quiet()
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


@contextmanager
def progress_display(
    state: ProgressState,
    *,
    console: Optional[Console] = None,
    enable_keys: bool = True,
) -> Iterator[ProgressState]:
    """Enter a Rich Live + key listener, yield the state, tear down on exit.

    Caller is expected to forward stream events to ``state.on_node_event``
    while inside the context.
    """
    console = console or Console()
    # Don't try to read keys from a non-interactive stdin (pytest, pipes).
    # Rich Live itself degrades gracefully on a non-terminal stdout.
    if not console.is_terminal:
        enable_keys = False
    renderable = _Renderable(state)
    listener: Optional[_KeyListener] = None
    with Live(
        renderable,
        console=console,
        refresh_per_second=4,
        transient=False,
    ):
        if enable_keys:
            listener = _KeyListener(state)
            listener.start()
        try:
            yield state
        finally:
            if listener is not None:
                listener.stop()
                listener.join(timeout=0.2)
