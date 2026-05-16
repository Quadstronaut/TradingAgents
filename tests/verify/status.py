"""Aggregate every persisted matrix-pass JSONL into a single status board.

Useful for `where do we stand?` queries after running individual
intents in separate invocations, or after a long ``--until-green``
session. Reads every ``tests/verify/.runs/*.jsonl`` and prints / writes
a roll-up of intent → green pass count, last red reason, total elapsed.

Usage:
    python -m tests.verify.status                 # board to stdout
    python -m tests.verify.status --md out.md     # markdown roll-up
    python -m tests.verify.status --since "2026-05-15"  # filter
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

DEFAULT_LOG_DIR = Path(__file__).parent / ".runs"


@dataclass
class IntentRollup:
    intent: str
    total: int = 0
    green: int = 0
    red: int = 0
    last_red_error: Optional[str] = None
    last_red_when: Optional[str] = None
    last_green_when: Optional[str] = None
    elapsed_total_sec: float = 0.0

    @property
    def green_rate(self) -> float:
        return self.green / self.total if self.total else 0.0


def iter_runs(log_dir: Path) -> Iterator[tuple[Path, dict]]:
    """Yield (jsonl_path, row_dict) for every persisted matrix row."""
    for jsonl in sorted(log_dir.glob("*.jsonl")):
        with jsonl.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield jsonl, json.loads(line)


def aggregate(
    log_dir: Path = DEFAULT_LOG_DIR,
    *,
    since: Optional[datetime.date] = None,
) -> dict[str, IntentRollup]:
    """Roll up every persisted row into per-intent stats."""
    rollups: dict[str, IntentRollup] = defaultdict(lambda: IntentRollup(""))
    for path, row in iter_runs(log_dir):
        if since is not None:
            # JSONL doesn't carry a wall-clock stamp per row; use the file
            # mtime as a proxy. Good enough for "show today's runs".
            mtime = datetime.date.fromtimestamp(path.stat().st_mtime)
            if mtime < since:
                continue
        intent = row.get("intent", "?")
        r = rollups[intent]
        r.intent = intent
        r.total += 1
        r.elapsed_total_sec += float(row.get("elapsed_sec", 0.0))
        green = bool(row.get("robust_ok")) and bool(row.get("shape_ok"))
        when = path.name.removesuffix(".jsonl")
        if green:
            r.green += 1
            r.last_green_when = when
        else:
            r.red += 1
            err = row.get("error") or ""
            lines = err.splitlines()
            r.last_red_error = (lines[0][:80] if lines else f"red w/o error (exit={row.get('exit_code')})")
            r.last_red_when = when
    return dict(rollups)


def print_board(rollups: dict[str, IntentRollup]) -> None:
    if not rollups:
        print("(no persisted runs found)")
        return
    print(f"{'intent':<18} {'total':>6} {'green':>6} {'red':>5} "
          f"{'green rate':>11}  {'avg elapsed':>12}  last red")
    print("-" * 100)
    for intent in sorted(rollups):
        r = rollups[intent]
        avg = r.elapsed_total_sec / r.total if r.total else 0.0
        rate = f"{100 * r.green_rate:5.1f}%"
        last_red = r.last_red_error or "—"
        print(
            f"{r.intent:<18} {r.total:>6} {r.green:>6} {r.red:>5} "
            f"{rate:>11}  {avg:>10.1f}s  {last_red}"
        )


def to_markdown(rollups: dict[str, IntentRollup]) -> str:
    lines = [
        "# Verify status roll-up",
        "",
        f"- generated: {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"- intents tracked: {len(rollups)}",
        "",
        "| Intent | Total | Green | Red | Green rate | Avg elapsed | Last red |",
        "|---|---|---|---|---|---|---|",
    ]
    for intent in sorted(rollups):
        r = rollups[intent]
        avg = r.elapsed_total_sec / r.total if r.total else 0.0
        rate = f"{100 * r.green_rate:.1f}%"
        last_red = (r.last_red_error or "—").replace("|", "\\|")
        lines.append(
            f"| {r.intent} | {r.total} | {r.green} | {r.red} | "
            f"{rate} | {avg:.1f}s | {last_red} |"
        )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    p.add_argument("--md", type=Path, default=None,
                   help="also write a markdown roll-up to this path")
    p.add_argument("--since", type=str, default=None,
                   help="filter rows to files at or after this date (YYYY-MM-DD)")
    args = p.parse_args(argv)
    since = datetime.date.fromisoformat(args.since) if args.since else None
    rollups = aggregate(args.log_dir, since=since)
    print_board(rollups)
    if args.md:
        args.md.write_text(to_markdown(rollups), encoding="utf-8")
        print(f"\n[md] wrote {args.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
