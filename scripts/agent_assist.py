"""CLI entry for the agent-assist orchestrator.

Usage:
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
from tradingagents.agent_assist.orchestrator import main as orchestrator_main


def _ollama_reachable() -> bool:
    """Probe the Ollama base URL; return True iff it responds within 3s."""
    # SHORTLIST_BASE_URL is .../v1; the version endpoint is on the root.
    root = SHORTLIST_BASE_URL.rstrip("/").removesuffix("/v1")
    try:
        urllib.request.urlopen(f"{root}/api/version", timeout=3)
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _parse_args(argv):
    p = argparse.ArgumentParser(description="Natural-language wrapper for TradingAgents runs.")
    p.add_argument("--prompt", required=True, help="Your natural-language ask.")
    p.add_argument("--budget", type=int, default=None,
                   help="Max per-share price in USD. If omitted and the prompt is a screen, "
                        "you'll be asked interactively.")
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

    return orchestrator_main(prompt=args.prompt, budget=args.budget)


if __name__ == "__main__":
    raise SystemExit(main())
