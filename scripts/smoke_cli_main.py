"""Smoke test for the cli.main.run_analysis path.

Drives the same code that `uv run tradingagents` runs interactively, but
bypasses the questionary keystroke layer by monkey-patching each helper
to return a canned answer. The graph execution, callbacks, message
buffer, rich.Live display, and per-section report writing are all real.

This is the smoke I should have run before the 2026-05-16 demo.

Usage:
    uv run python scripts/smoke_cli_main.py
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Force UTF-8 on Windows consoles so rich.Live teardown doesn't blow up.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _drive(ticker: str, deep_model: str, quick_model: str, depth: int) -> int:
    today = datetime.date.today().isoformat()

    # Import inside the function so the monkey-patches apply before the
    # CLI module's first reference to these helpers.
    from cli import main as cli_main
    from cli.models import AnalystType
    import typer

    # cli/main.py does `from cli.utils import *`, so questionary helpers are
    # bound as attributes of the cli.main module — patching cli.utils.X is
    # too late once star-import has copied the binding. Patch on cli.main.
    cli_main.get_ticker = lambda: ticker
    cli_main.get_analysis_date = lambda: today
    cli_main.ask_output_language = lambda: "English"
    cli_main.select_analysts = lambda: [
        AnalystType.MARKET, AnalystType.SOCIAL,
        AnalystType.NEWS, AnalystType.FUNDAMENTALS,
    ]
    cli_main.select_research_depth = lambda: depth
    cli_main.select_llm_provider = lambda: ("Ollama", "http://localhost:11434/v1")
    cli_main.select_shallow_thinking_agent = lambda provider: quick_model
    cli_main.select_deep_thinking_agent = lambda provider: deep_model
    cli_main.ask_openai_reasoning_effort = lambda: None
    cli_main.ask_anthropic_effort = lambda: None
    cli_main.ask_gemini_thinking_config = lambda: None
    typer.prompt = lambda *_a, **_k: "N"

    t0 = time.monotonic()
    print(f"[smoke_cli_main] starting: ticker={ticker} depth={depth} "
          f"quick={quick_model} deep={deep_model}", flush=True)
    try:
        cli_main.run_analysis(checkpoint=False)
        rc = 0
    except SystemExit as e:
        rc = int(e.code) if e.code is not None else 0
    elapsed = round(time.monotonic() - t0, 1)
    print(f"[smoke_cli_main] done in {elapsed}s rc={rc}", flush=True)

    reports_dir = Path.home() / ".tradingagents" / "logs" / ticker / today / "reports"
    if reports_dir.exists():
        files = sorted(reports_dir.glob("*.md"))
        print(f"[smoke_cli_main] reports written: {len(files)} files in {reports_dir}", flush=True)
        for f in files:
            size = f.stat().st_size
            print(f"  - {f.name}  ({size} bytes)", flush=True)
    else:
        print(f"[smoke_cli_main] WARNING: no reports dir at {reports_dir}", flush=True)

    return rc


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", default="NVDA")
    p.add_argument("--depth", type=int, default=1, help="1=Shallow, 3=Deep")
    p.add_argument("--quick", default="qwen3:8b")
    p.add_argument("--deep", default="qwen3-coder:30b")
    args = p.parse_args()
    return _drive(args.ticker, args.deep, args.quick, args.depth)


if __name__ == "__main__":
    sys.exit(main())
