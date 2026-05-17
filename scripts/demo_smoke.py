"""Demo-day smoke: 2 news scans + 2 deep runs on NVDA, in series.

Throwaway. Pins pick_ticker to NVDA so each pass hits the same name,
which is what we want for a known-ticker demo dry run.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

# Repo root on sys.path so `from tests.verify ...` resolves outside pytest.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Force UTF-8 on Windows consoles so rich.Live teardown doesn't crash.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from tests.verify import runner
from tests.verify.runner import pass_result_to_dict


OUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "verify" / ".runs" / "demo-smoke"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _log(label: str, msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {label}: {msg}"
    print(line, flush=True)


def main() -> int:
    started = time.monotonic()
    results: list[dict] = []
    with patch.object(runner, "pick_ticker", lambda *_a, **_k: "NVDA"):
        for i, (name, fn) in enumerate([
            ("news_scan_1", runner.verify_news_scan),
            ("news_scan_2", runner.verify_news_scan),
            ("specific_1", runner.verify_specific),
            ("specific_2", runner.verify_specific),
        ]):
            _log(name, "starting")
            t0 = time.monotonic()
            try:
                r = fn(i, output_dir=OUT_DIR)
                d = pass_result_to_dict(r)
            except Exception as e:
                d = {"label": name, "fatal": f"{type(e).__name__}: {e}"}
            d["label"] = name
            d["wall_sec"] = round(time.monotonic() - t0, 1)
            results.append(d)
            _log(name, f"done in {d['wall_sec']}s — robust={d.get('robust_ok')} shape={d.get('shape_ok')} ratings={d.get('ratings')}")
            (OUT_DIR / "results.jsonl").open("a", encoding="utf-8").write(json.dumps(d) + "\n")

    total = round(time.monotonic() - started, 1)
    _log("ALL", f"total={total}s")
    summary = OUT_DIR / "summary.md"
    with summary.open("w", encoding="utf-8") as fh:
        fh.write(f"# Demo smoke — NVDA\n\nTotal: {total}s\n\n")
        for d in results:
            ok = d.get("robust_ok") and d.get("shape_ok")
            mark = "OK" if ok else "FAIL"
            fh.write(f"- {mark} **{d.get('label')}** ({d.get('wall_sec')}s) — ratings={d.get('ratings')} shape_ok={d.get('shape_ok')}\n")
            if d.get("error"):
                fh.write(f"    - error: {d['error'][:300]}\n")
    _log("ALL", f"summary written to {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
