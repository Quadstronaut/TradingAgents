"""Render run results to console + a markdown summary file."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Lower number = better (Buy outranks Sell). Non-decision states sort last.
_RANK = {
    "Buy": 0,
    "Overweight": 1,
    "Hold": 2,
    "Underweight": 3,
    "Sell": 4,
    "SKIPPED": 5,
    "FAILED": 6,
}


@dataclass(frozen=True)
class RunResult:
    ticker: str
    rating: str                    # Buy / Overweight / Hold / Underweight / Sell / SKIPPED / FAILED
    log_path: Optional[Path]
    error: Optional[str]           # populated for FAILED
    decision_md: Optional[str] = None  # full Portfolio Manager decision markdown (compare flow uses this)


def rank_results(results: list[RunResult]) -> list[RunResult]:
    return sorted(results, key=lambda r: _RANK.get(r.rating, 99))


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 50) -> str:
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "run"


def write_summary(prompt: str, results: list[RunResult], output_dir: Path) -> Path:
    """Write a markdown summary; return the file path."""
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = output_dir / f"{ts}-{_slugify(prompt)}.md"

    ranked = rank_results(results)

    lines = [
        f"# Agent-assist run — {ts}",
        "",
        f"**Prompt:** {prompt}",
        "",
        "## Ranked results",
        "",
        "| Ticker | Rating | Log dir |",
        "|---|---|---|",
    ]
    for r in ranked:
        log = str(r.log_path) if r.log_path else "—"
        lines.append(f"| {r.ticker} | {r.rating} | {log} |")

    lines.append("")
    lines.append("## Per-ticker detail")
    lines.append("")
    for r in ranked:
        lines.append(f"### {r.ticker} — {r.rating}")
        if r.error:
            lines.append("")
            lines.append(f"Error: `{r.error}`")
        if r.log_path:
            lines.append("")
            lines.append(f"Log dir: `{r.log_path}`")
        lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out
