"""Interactive flat-picker menu for agent-assist.

Replaces the free-form prompt as the default entry point. Each menu
option collects only the slots its flow needs and produces a Task
that the orchestrator routes to the right flow.

Slot prompts are deliberately simple ``input()`` calls (not PowerShell
parameters) so that variable-interpolation footguns like
``"If I had $100..."`` becoming ``"If I had ..."`` cannot happen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional

from tradingagents.agent_assist.prompt_parse import normalize_ticker

Intent = Literal[
    "specific",   # 1) Analyze a specific ticker
    "theme",      # 2) Who's big in <theme>
    "budget",     # 3) What can I do with $N
    "owned",      # 4) Decide on a position I already own
    "compare",    # 5) Compare two tickers
    "news_scan",  # 6) News & sentiment scan
    "freeform",   # 7) Write your own
]


@dataclass(frozen=True)
class Task:
    """Routed user intent + slots for the orchestrator."""

    intent: Intent
    ticker: Optional[str] = None
    ticker_b: Optional[str] = None          # for compare
    theme: Optional[str] = None
    budget: Optional[int] = None
    shares: Optional[float] = None
    cost_basis: Optional[float] = None
    prompt: Optional[str] = None            # freeform path

    def display(self) -> str:
        """Short human-readable description for the confirm step."""
        if self.intent == "specific":
            return f"deep analysis on {self.ticker} (~15 min)"
        if self.intent == "theme":
            budget = f", under ${self.budget}/share" if self.budget else ""
            return f"theme screen for '{self.theme}'{budget}, then deep analysis on top 2-3 (~30-45 min)"
        if self.intent == "budget":
            theme = f" focused on '{self.theme}'" if self.theme else ""
            return f"budget screen at ${self.budget}/share{theme}, then deep analysis on top 2-3 (~30-45 min)"
        if self.intent == "owned":
            return (
                f"deep analysis on {self.ticker} with position context "
                f"({self.shares:g} shares @ ${self.cost_basis:.2f} basis, ~15 min)"
            )
        if self.intent == "compare":
            return f"head-to-head: deep analysis on {self.ticker} and {self.ticker_b}, then comparison (~30 min)"
        if self.intent == "news_scan":
            return f"news & sentiment scan on {self.ticker} (~5 min, no rating)"
        if self.intent == "freeform":
            return f"free-form: {self.prompt!r}"
        return self.intent


# ---------------------------------------------------------------------------
# Slot collection helpers
# ---------------------------------------------------------------------------


class _Cancelled(Exception):
    """User typed 'b' to go back to the menu."""


def _ask(prompt: str, *, allow_back: bool = True) -> str:
    """Prompt for input. Raises _Cancelled if user types 'b' or 'back'."""
    raw = input(prompt).strip()
    if allow_back and raw.lower() in ("b", "back"):
        raise _Cancelled()
    return raw


def _ask_ticker(label: str = "Ticker") -> str:
    """Prompt for a ticker, normalised to canonical form. Re-prompts on empty."""
    while True:
        raw = _ask(f"{label} (e.g. NVDA, BRK.B): ")
        if not raw:
            print("  Ticker can't be empty. (Type 'b' to go back.)")
            continue
        return normalize_ticker(raw)


def _ask_int(label: str, minimum: Optional[int] = None) -> int:
    while True:
        raw = _ask(f"{label}: ").lstrip("$")
        try:
            v = int(float(raw))
        except ValueError:
            print(f"  '{raw}' is not a number.")
            continue
        if minimum is not None and v < minimum:
            print(f"  Must be at least {minimum}.")
            continue
        return v


def _ask_float(
    label: str,
    minimum: Optional[float] = None,
    *,
    strict_min: bool = False,
) -> float:
    """Prompt for a float; reprompts on non-numeric or out-of-range.

    ``minimum`` is treated as a strict lower bound when ``strict_min`` is
    True (value must be greater than minimum) — used for share counts
    where 0 is meaningless ("I hold 0 shares" is not a held position).
    """
    while True:
        raw = _ask(f"{label}: ").lstrip("$")
        try:
            v = float(raw)
        except ValueError:
            print(f"  '{raw}' is not a number.")
            continue
        if minimum is not None:
            if strict_min and v <= minimum:
                print(f"  Must be greater than {minimum}.")
                continue
            if not strict_min and v < minimum:
                print(f"  Must be at least {minimum}.")
                continue
        return v


def _ask_optional(label: str) -> Optional[str]:
    """Prompt; Enter returns None."""
    raw = _ask(f"{label} (Enter to skip): ")
    return raw or None


def _confirm(task: Task) -> bool:
    """Show the routed plan and ask y/n."""
    print()
    print(f"Will run {task.display()}.")
    while True:
        raw = input("Proceed? [y/n]: ").strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no", "b", "back"):
            return False
        print("  Answer y or n.")


# ---------------------------------------------------------------------------
# Per-option slot collectors
# ---------------------------------------------------------------------------


def _collect_specific() -> Task:
    return Task(intent="specific", ticker=_ask_ticker())


def _collect_theme() -> Task:
    theme = _ask("Theme or sector (e.g. AI chips, biotech): ")
    while not theme:
        print("  Theme can't be empty.")
        theme = _ask("Theme or sector: ")
    raw_budget = _ask_optional("Max $/share")
    budget = int(float(raw_budget.lstrip("$"))) if raw_budget else None
    return Task(intent="theme", theme=theme, budget=budget)


def _collect_budget() -> Task:
    budget = _ask_int("Max $/share", minimum=1)
    theme = _ask_optional("Focus on a sector (e.g. tech, biotech)")
    return Task(intent="budget", budget=budget, theme=theme)


def _collect_owned() -> Task:
    ticker = _ask_ticker()
    # 0 shares is not a held position — strict_min so the PM prompt never
    # sees "User currently holds 0 shares of NVDA".
    shares = _ask_float(
        f"How many shares of {ticker} do you hold?",
        minimum=0, strict_min=True,
    )
    cost_basis = _ask_float("Cost basis per share ($)", minimum=0)
    return Task(intent="owned", ticker=ticker, shares=shares, cost_basis=cost_basis)


def _collect_compare() -> Task:
    a = _ask_ticker("First ticker")
    b = _ask_ticker("Second ticker")
    while b == a:
        print("  Pick a different ticker for the second slot.")
        b = _ask_ticker("Second ticker")
    return Task(intent="compare", ticker=a, ticker_b=b)


def _collect_news_scan() -> Task:
    return Task(intent="news_scan", ticker=_ask_ticker())


def _collect_freeform() -> Task:
    prompt = _ask("Your prompt: ")
    while not prompt:
        print("  Prompt can't be empty.")
        prompt = _ask("Your prompt: ")
    return Task(intent="freeform", prompt=prompt)


# ---------------------------------------------------------------------------
# Menu rendering
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Option:
    label: str
    collector: Callable[[], Task]


_OPTIONS: list[_Option] = [
    _Option("Analyze a specific ticker", _collect_specific),
    _Option("Who's big in <theme> lately", _collect_theme),
    _Option("What can I do with $<N> this week", _collect_budget),
    _Option("Decide on a position I already own", _collect_owned),
    _Option("Compare two tickers head-to-head", _collect_compare),
    _Option("News & sentiment scan (no full debate, ~5 min)", _collect_news_scan),
    _Option("Write your own prompt (advanced)", _collect_freeform),
]


def _render() -> None:
    print()
    print("What would you like to do?")
    print()
    for i, opt in enumerate(_OPTIONS, start=1):
        print(f"  {i}) {opt.label}")
    print("  q) Quit")
    print()


def run_menu() -> Optional[Task]:
    """Render the menu loop until the user confirms a task or quits.

    Returns the chosen Task, or None if the user quit.

    Raises KeyboardInterrupt if the user hits Ctrl-C — caller is
    expected to handle that as a clean exit.
    """
    while True:
        _render()
        raw = input("> ").strip().lower()
        if raw in ("q", "quit", "exit"):
            return None
        try:
            idx = int(raw)
        except ValueError:
            print(f"  '{raw}' isn't an option. Pick a number or 'q'.")
            continue
        if not (1 <= idx <= len(_OPTIONS)):
            print(f"  {idx} is out of range. Pick 1-{len(_OPTIONS)} or 'q'.")
            continue

        opt = _OPTIONS[idx - 1]
        try:
            task = opt.collector()
        except _Cancelled:
            continue  # back to menu

        if _confirm(task):
            return task
        # else: fall back to menu
