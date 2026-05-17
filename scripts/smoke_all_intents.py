"""Smoke test for every agent_assist intent.

Drives each intent end-to-end via the same orchestrator code that
agent.ps1 invokes, with scripted confirm-prompt answers so multi-ticker
intents finish in bounded time. Pins ticker to NVDA where applicable.

Wall time budget: ~1.5 hours (1 deep per intent for single-ticker
intents; 1 deep + 2 skips per multi-ticker intent).

Usage:
    uv run python scripts/smoke_all_intents.py
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from tests.verify import runner
from tests.verify.runner import pass_result_to_dict


OUT_DIR = _REPO_ROOT / "tests" / "verify" / ".runs" / "all-intents"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.date.today().isoformat()


def _log(label: str, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {label}: {msg}", flush=True)


# Each entry: (label, intent, inputs, scripted_inputs, expect_deep_runs).
# scripted_inputs are consumed by orchestrator.input() calls; "y" runs the
# next deep, "s" skips it, "a" aborts the remaining candidates.
CASES = [
    # Single-ticker intents — one deep, just confirm.
    ("01-specific-NVDA",        "specific",        {"ticker": "NVDA",                             "today": TODAY}, ["y"],          True),
    ("02-news_scan-NVDA",       "news_scan",       {"ticker": "NVDA",                             "today": TODAY}, [],             False),
    ("03-owned-NVDA",           "owned",           {"ticker": "NVDA", "shares": 10.0, "cost_basis": 100.0, "today": TODAY}, ["y"], True),
    ("04-freeform_single-NVDA", "freeform",        {"prompt": "What do you think about NVDA right now?", "ticker_hint": "NVDA", "today": TODAY}, ["y"], True),

    # Multi-ticker intents — run the first candidate, skip the rest to keep
    # wall time bounded. The full flow still exercises shortlist + confirm
    # + summary write.
    ("05-compare-NVDA-MSFT",    "compare",         {"ticker": "NVDA", "ticker_b": "MSFT",         "today": TODAY}, ["y", "s"],     True),
    ("06-theme-tech",           "theme",           {"theme": "AI semiconductor companies", "budget": 200, "today": TODAY}, ["y", "s", "s"], True),
    ("07-budget-100",           "budget",          {"budget": 100, "theme": None,                 "today": TODAY}, ["y", "s", "s"], True),
    ("08-freeform_screen",      "freeform",        {"prompt": "small-cap AI plays under $50",     "budget": 150, "today": TODAY}, ["y", "s", "s"], True),
]


def main() -> int:
    started = time.monotonic()
    results: list[dict] = []
    # Force the rotor to NVDA for any test that asks for a deterministic
    # ticker via pick_ticker (some intents don't take a ticker explicitly).
    with patch.object(runner, "pick_ticker", lambda *_a, **_k: "NVDA"):
        for label, intent, inputs, scripted, expect_deep in CASES:
            _log(label, "starting")
            t0 = time.monotonic()
            try:
                r = runner._wrap(
                    pass_no=0, intent=intent, inputs=inputs,
                    output_dir=OUT_DIR / label,
                    scripted_inputs=scripted,
                    expect_deep_runs=expect_deep,
                )
                d = pass_result_to_dict(r)
            except Exception as e:
                import traceback
                d = {
                    "label": label, "intent": intent, "fatal": f"{type(e).__name__}: {e}",
                    "traceback": traceback.format_exc(),
                }
            d["label"] = label
            d["wall_sec"] = round(time.monotonic() - t0, 1)
            results.append(d)
            ratings = d.get("ratings") or []
            ok = d.get("robust_ok", False) and d.get("shape_ok", False)
            mark = "OK" if ok else "FAIL"
            _log(label, f"{mark} in {d['wall_sec']}s ratings={ratings} "
                        f"robust={d.get('robust_ok')} shape={d.get('shape_ok')}")
            if d.get("error"):
                _log(label, f"  error={d['error'][:200]}")
            (OUT_DIR / "results.jsonl").open("a", encoding="utf-8").write(json.dumps(d) + "\n")

    total = round(time.monotonic() - started, 1)
    _log("ALL", f"total={total}s")

    # Final summary markdown.
    md = OUT_DIR / "summary.md"
    with md.open("w", encoding="utf-8") as fh:
        fh.write(f"# All-intents smoke — {datetime.datetime.now().isoformat()}\n\n")
        fh.write(f"Total: {total}s\n\n")
        fh.write("| # | Intent | Wall (s) | Robust | Shape | Ratings |\n")
        fh.write("|---|---|---|---|---|---|\n")
        for d in results:
            fh.write(f"| {d.get('label')} | {d.get('intent', '?')} | {d.get('wall_sec')} | "
                     f"{d.get('robust_ok')} | {d.get('shape_ok')} | {d.get('ratings')} |\n")
        fh.write("\n## Errors\n\n")
        any_err = False
        for d in results:
            if d.get("error") or d.get("fatal"):
                any_err = True
                fh.write(f"### {d.get('label')}\n\n")
                if d.get("error"):
                    fh.write(f"- error: {d['error']}\n")
                if d.get("fatal"):
                    fh.write(f"- fatal: {d['fatal']}\n")
                if d.get("traceback"):
                    fh.write(f"\n```\n{d['traceback']}\n```\n")
        if not any_err:
            fh.write("(none)\n")
    _log("ALL", f"summary -> {md}")

    return 0 if all(d.get("robust_ok") for d in results) else 1


if __name__ == "__main__":
    sys.exit(main())
