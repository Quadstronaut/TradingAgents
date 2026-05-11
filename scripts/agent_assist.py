"""CLI entry for the agent-assist orchestrator.

Two modes:

    # Interactive guided menu (default)
    uv run python scripts/agent_assist.py

    # Free-form bypass (legacy; same path as menu option 7)
    uv run python scripts/agent_assist.py --prompt "should I buy NVDA"
    uv run python scripts/agent_assist.py --prompt "tech under 100" --budget 100
"""

from __future__ import annotations

import argparse
import logging
import sys
import urllib.error
import urllib.request

from tradingagents.agent_assist.config import SHORTLIST_BASE_URL
from tradingagents.agent_assist.menu import run_menu
from tradingagents.agent_assist.orchestrator import main as freeform_main
from tradingagents.agent_assist.orchestrator import run_task


def _ollama_reachable() -> bool:
    """Probe the Ollama base URL; return True iff it responds within 3s."""
    root = SHORTLIST_BASE_URL.rstrip("/").removesuffix("/v1")
    try:
        urllib.request.urlopen(f"{root}/api/version", timeout=3)
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _parse_args(argv):
    p = argparse.ArgumentParser(description="Natural-language wrapper for TradingAgents runs.")
    p.add_argument(
        "--prompt", default=None,
        help="Free-form prompt (legacy). If omitted, launches the interactive menu.",
    )
    p.add_argument(
        "--budget", type=int, default=None,
        help="Max per-share price in USD. Only used with --prompt.",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)

    if not _ollama_reachable():
        root = SHORTLIST_BASE_URL.rstrip("/").removesuffix("/v1")
        print(
            f"Ollama not reachable at {root}.\n"
            "Probe with:  curl http://localhost:11434/api/version\n"
            "If it's down, relaunch the Ollama tray app.",
            file=sys.stderr,
        )
        return 2

    if args.prompt:
        return freeform_main(prompt=args.prompt, budget=args.budget)

    # Interactive menu path.
    try:
        task = run_menu()
    except KeyboardInterrupt:
        print()
        return 130
    if task is None:
        return 0

    try:
        return run_task(task)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
