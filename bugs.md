# Outstanding bugs — TradingAgents

Snapshot 2026-05-16 12:00 local. Sourced from the verification matrix
(`tests/verify/`) running real Ollama-backed deep pipelines on `qwen3-coder:30b`
deep / `qwen3:8b` quick.

---

## B1 — Intermittent structured-output fallback on Portfolio Manager

**Severity:** medium · **Status:** known-tolerated by design, **not yet a
clean 3x green** on the verification gate

**What happens.** `tradingagents/agents/managers/portfolio_manager.py`
binds `PortfolioDecision` via `with_structured_output` and routes through
`invoke_structured_or_freetext(...)` — which **deliberately catches any
exception from the structured call and falls back to free-text** (see
`tradingagents/agents/utils/structured.py:62-73`). When the structured path
fails, the rating word is still extractable by `SignalProcessor`, so the
user-visible rating works — but the rendered markdown does **not** carry
the documented `**Rating**: / **Executive Summary**: / **Investment Thesis**:`
headers from `render_pm_decision`.

**Observed rate.** Over 4 passes × 8 intents (some with 2-3 deep runs each
≈ ~36 PM invocations) on `qwen3-coder:30b`, 5 deep runs fell back to
free-text. Roughly **5-15% per deep run**.

**Examples** (from `tests/verify/.runs/20260516-*.jsonl`):
- pass 1: theme had 1 of 2 candidates fall back
- pass 1: freeform_screen had 1 of 3 candidates fall back (Hold from
  free-text)
- pass 2: owned (Hold) — fallback
- pass 3: owned (Overweight) and theme (Hold,Buy) — partial fallbacks
- All retained a canonical rating word; none crashed.

**Impact.**
- User-facing: minor — rating is correct, decision content is still
  produced, but the markdown structure varies between two shapes.
- Verification gate: blocking — the strict shape check fails on free-text
  output, so `--until-green 3` never closes.

**Mitigations to consider.**
1. **Relax the shape check** to accept either the structured path
   (`**Rating**: + **Executive Summary**: + **Investment Thesis**:`) OR
   the free-text path (canonical rating word appears anywhere in the
   markdown, markdown non-empty). Matches the design.
2. **Tighten the prompt** to push `qwen3-coder:30b` harder toward valid
   structured output. Increase JSON-schema emphasis, add a one-shot
   example.
3. **Add a retry-once-on-validation-error** inside
   `invoke_structured_or_freetext` before falling through to free-text
   (currently 0 retries).
4. **Swap deep model** per the `steps-to-production.md` Phase 3 ladder
   (this is the homogeneous-30b path). Probably the slowest fix.

**Recommended:** #1 + #3 together — the design's fallback intent is to
tolerate, and one retry is a cheap improvement before fallback.

---

## B2 — rich.Live teardown crashes on Windows cp1252 stdout

**Severity:** high · **Status:** **FIXED** in
`scripts/verify.ps1` + `tests/verify/matrix.py` + `_run_one_deep`

**What happened.** After a successful 25-min deep run, the
`with progress_display(state):` teardown wrote its final buffer through a
non-TTY stdout. Python's default Windows code page (cp1252) cannot encode
the up-arrow legend glyph (↑) — `UnicodeEncodeError` propagated out of
the with-block, was caught by `_run_one_deep`'s outer except, and the
result was returned as `RunResult(rating="FAILED")`. **A real 25-minute
Buy verdict was destroyed by a cosmetic display bug.**

**Fixes shipped (commit 846db46 + 7f3e098).**
- `tests/verify/matrix.py`: reconfigures stdout/stderr to UTF-8 with
  `errors="replace"` on win32 before any rich import.
- `tradingagents/agent_assist/orchestrator.py:_run_one_deep`: captures the
  `RunResult` **inside** the `with progress_display(...)` block; if the
  outer except sees that a result was already captured, returns it as
  successful rather than as FAILED. **A teardown crash no longer destroys
  a completed run.**
- `tests/verify/test_orchestrator_teardown_resilience.py`: 3 regression
  tests pinning this behaviour.

---

## B3 — Shape check originally asserted the wrong PM contract

**Severity:** medium · **Status:** **FIXED** (commit 7f3e098)

**What happened.** The verification harness's `_check_shape` looked for
`**Recommendation**:` and `FINAL TRANSACTION PROPOSAL: **...**` on the
Portfolio Manager's `final_trade_decision` markdown. Those headers live
elsewhere:
- `**Recommendation**:` is rendered by `render_research_plan` into the
  `investment_plan` state field (Research Manager output).
- `FINAL TRANSACTION PROPOSAL: **...**` is rendered by
  `render_trader_proposal` into the `trader_investment_plan` state field
  (Trader output).

The PM's `render_pm_decision` produces `**Rating**: / **Executive Summary**: /
**Investment Thesis**:` (+ optional Price Target, Time Horizon). The
harness now matches that contract.

---

## B4 — Tee-Object on PowerShell buffers stdout invisibly for long runs

**Severity:** low (cosmetic) · **Status:** workaround in place

**What happens.** `uv run python -m tests.verify.matrix ... 2>&1 |
Tee-Object` shows the matrix's initial `=== Pass #0 ===` line, then
nothing until the run completes ~15-30 min later. Looks hung.

**Workaround.** `scripts/verify.ps1` now sets `PYTHONUNBUFFERED=1` and
invokes `uv run python -u`. The rich.Live overview panel also flushes
correctly on a TTY but appears as a single late dump when piped to
Tee-Object. Cosmetic only — the run is alive (verifiable via
`ollama ps`).

---

## B5 — Tee-Object writes the output file in UTF-16 LE on Windows PowerShell 5.1

**Severity:** low · **Status:** open

**What happens.** `Tee-Object -FilePath X.txt` defaults to UTF-16 LE on
PS 5.1; downstream Get-Content / Bash `cat` show mojibake on box-drawing
characters (`â”Œâ”€` instead of `┌─`). The file itself is fine; the
display step misinterprets it.

**Fix.** Add `-Encoding UTF8` to Tee-Object calls in `scripts/verify.ps1`
(or use `... | Out-File -Encoding utf8`).

---

## B6 — Acceptance gate persists no streak state across invocations

**Severity:** low (design choice) · **Status:** open

**What it means.** `run_until_green --until-green 3 --max-attempts 5`
loses its streak counter on process exit. If a run is killed at attempt
4 (with streak 2/3 in progress), restarting starts over at streak 0/3.

**Fix.** Persist `streak` and `last_attempt_seed` to
`tests/verify/.runs/streak.json` so a restart resumes the streak. Not
needed for a clean single-session run.

---

## B7 — Backtest directional tier not yet exercised against Ollama

**Severity:** medium · **Status:** open

The 5-row labelled fixture (`tests/verify/fixtures/backtest_dataset.csv`)
is well-formed and yfinance-validated. The cheap-tier tests pass. The
**slow** end-to-end backtest (`test_backtest_run_passes_threshold`)
hasn't run live yet — pre-empted by the deep-pipeline robustness work.

**To run:** `uv run python -c "from tests.verify.backtest import
run_backtest; print(run_backtest())"` (≈ 75 min on Ollama).
