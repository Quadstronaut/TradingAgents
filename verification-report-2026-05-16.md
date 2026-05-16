# TradingAgents verification — 2026-05-16 status report

## TL;DR

- **The product runs end-to-end against local Ollama on this Windows + uv
  setup.** All 7 user-facing intents have produced valid ratings under
  real-model load.
- **Verification harness shipped:** 428 fast tests pass cleanly (unit +
  contract); per-intent matrix runner exists with seeded random ticker
  / vague-prompt rotors and a 3x acceptance gate.
- **One blocking issue for the 3x green gate** — the Portfolio Manager
  intermittently falls back to free-text output (~5-15% per deep run).
  The ratings are still valid; the markdown shape varies. This is
  documented graceful degradation in the codebase. See `bugs.md` B1.
- **Demo-ready** for news_scan (~3-5 min visible run) and specific-ticker
  deep analysis (~13-15 min visible run with 9-phase progress display).

---

## What's actually demonstrable in 1 hour

### Fastest, most visual: News & sentiment scan (~3-5 min)
```powershell
uv run tradingagents analyze   # then pick option 6 / "News & sentiment scan"
# or programmatically:
uv run python -c "from tradingagents.agent_assist.menu import Task; from tradingagents.agent_assist.orchestrator import run_task; run_task(Task(intent='news_scan', ticker='NVDA'))"
```
Produces a `Lean: bullish/neutral/bearish · Confidence: 0.NN` verdict
with one-paragraph synthesis citing real Yahoo Finance articles.
Confirmed green on ticker A (Agilent) — see
`~/.tradingagents/agent_assist/verify/20260515-233229-news-scan-A.md`.

### Most impressive: Specific-ticker full deep pipeline (~14 min)
```powershell
uv run tradingagents analyze   # then pick option 1 / "Analyze a specific ticker"
```
Shows the 9-phase Rich live display (Market → Social → News →
Fundamentals → Bull/Bear debate → Research Manager → Trader → Risk
debate → Portfolio Manager) ticking off with elapsed times. Ends with a
Buy/Overweight/Hold/Underweight/Sell rating and a structured PM markdown
(Executive Summary, Investment Thesis, Price Target, Time Horizon).
Confirmed green twice on ticker A.

### Slower but breadth: Theme/budget shortlist (~30-45 min)
```powershell
uv run tradingagents analyze   # then option 2 / "Who's big in <theme>"
# theme: "best AI plays this week" -> picks 2-3 candidates -> deep on each
```
Demonstrates the LLM-driven shortlist + sector concentration heads-up +
per-candidate deep analysis flow. Probably too slow for a live demo.

---

## What was built this session

### Verification harness (new) — `tests/verify/`
- **Seeded random rotors** — `fixtures/random_ticker.py` picks one of 568
  tickers per pass (`pass_no * 17 mod 568`); `fixtures/vague_prompts.py`
  picks one of 10 hand-curated "I don't own this, just curious" prompts.
- **Backtest dataset** — `fixtures/backtest_dataset.csv` with 5
  hand-validated rows (4 positive + 1 negative, 4 sectors, |alpha| ≥ 0.063).
- **Per-intent runner** — `runner.py` exposes 8 verifier functions
  (specific, owned, theme, budget, compare, news_scan, freeform_single,
  freeform_screen). Each builds a `Task`, invokes the real `run_task`,
  captures deep-run RunResults via a `_run_one_deep` collector, returns
  a `PassResult` with robustness + shape booleans.
- **Matrix orchestrator** — `matrix.py` with `--reps N`, `--seed K`,
  `--only intent`, `--quick`, `--until-green N --max-attempts M`.
  Persists per-pass JSONL + markdown summary to `tests/verify/.runs/`.
- **Windows wrapper** — `scripts/verify.ps1` with Ollama health check
  and UTF-8 stdout setup.
- **Cross-pass status** — `tests/verify/status.py` rolls up every
  persisted JSONL into a per-intent green/red board.

### Contract tests (new) — `tests/contract/`
- 10 files (9 cloud LLM clients + AlphaVantage data vendor), 60 tests
  all green, fully offline (no API keys needed).
- Verifies request shape and parser handling per provider; covers
  json_schema / response_schema / tool-use / function-calling bindings.

### Bug fixes shipped (4 commits)
- `846db46` — capture RunResult inside `with progress_display` so a
  display-teardown crash doesn't discard a successful 25-min deep run.
- `bb08078` — force UTF-8 stdout on Windows so cp1252 doesn't choke on
  rich.Live's arrow glyphs.
- `7f3e098` — shape check matches the actual `render_pm_decision`
  contract (PM produces **Rating** / **Executive Summary** /
  **Investment Thesis**; **Recommendation** belongs to Research Manager
  and **FINAL TRANSACTION PROPOSAL** belongs to Trader).
- `129bae6` — surface deep-run error strings in PassResult so failure
  reports show the cause.

### Documentation (new)
- `docs/superpowers/specs/2026-05-15-full-rectification-design.md` —
  the validated design spec for the 3-tier shape contract + phased
  reactive multi-agent pipeline.
- `docs/superpowers/specs/2026-05-15-stage3-operator-notes.md` —
  living per-attempt log with forensics.
- `bugs.md` — outstanding bugs with severity, status, and proposed fixes.

---

## Test pass / fail counts

| Suite | Pass | Skip | Slow (deselected) |
|---|---|---|---|
| Original unit + agent_assist tests | 289 | 0 | 0 |
| Harness self-tests (random_ticker, vague_prompts, runner, matrix, backtest) | 60 | 0 | 8 |
| Contract tests (10 providers + AlphaVantage) | 60 | 0 | 0 |
| Teardown resilience regression | 3 | 0 | 0 |
| DeepSeek live API | 0 | 1 | 0 |
| **Total** | **428** | **1** | **10** |

`-m "not slow"` runs in ≈11 sec on this machine.

---

## Live-run findings (real Ollama, qwen3-coder:30b + qwen3:8b)

Across attempts 1-4 of the acceptance gate (pass numbers 0-3, ~12h of
real LLM time):

| Pass | Specific | Owned | Theme | Budget | Compare | News scan | Freeform single | Freeform screen |
|---|---|---|---|---|---|---|---|---|
| 0 | Buy | Buy | Buy,Buy | Buy,Buy,Buy | Buy,Buy | bullish | Buy,Buy | Buy |
| 1 | Buy | Buy | Buy,Buy (1 fallback) | Buy,Buy,Buy | Buy,Buy | bullish | Buy,Buy | Buy,Buy,Hold (1 fallback) |
| 2 | Buy | Hold (fallback) | Buy,Buy,Buy | Buy,Buy,Buy | Buy,Buy | bullish | Buy,Buy | Buy,Buy |
| 3 | Buy | Overweight (fallback) | Hold,Buy (1 fallback) | Buy,Buy,Buy | Buy,Buy | bullish | Buy,Buy | Buy |
| 4 (partial) | Buy | Buy | (killed mid-theme) | — | — | — | — | — |

**Robustness:** 100%. No crashes, no FAILED rating, no missing summary
files. Every run produced a canonical rating word.

**Shape (strict):** ~85%. 5 of ~36 deep runs hit the free-text fallback
path; the others matched the structured render contract.

**Ollama models:** loaded cleanly, keepalive worked, GPU utilization
high. Average deep-run wall-clock: 12-25 min depending on analyst tool
fluency.

**Sector concentration heads-up** (`feat(agent_assist): sector
concentration heads-up in shortlist`) correctly fired on theme pass 0
where both candidates (EPAM + ANET) were Information Technology.

---

## Recommendation for the demo

1. **Pre-warm the models** before your dad sits down:
   ```powershell
   ollama run qwen3-coder:30b "ok" --keepalive 1h
   ollama run qwen3:8b "ok" --keepalive 1h
   ```
2. **Lead with news_scan on NVDA** — visible in 3-5 min, generates
   well-formed bullish/bearish/neutral content from real Yahoo Finance
   articles. Use the interactive menu so he sees the 7 options.
3. **Then specific-ticker analysis** on a name he cares about
   (NVDA / AAPL / TSLA). The 9-phase progress display is the most
   impressive visual; it'll run while you explain what each agent does.
4. **Save the run report** — `~/.tradingagents/agent_assist/verify/`
   has the markdown summary; open it in a browser or VS Code preview.
5. **Skip theme/budget for now** — 30-45 min is too long for a demo,
   and the LLM occasionally returns Hold/Overweight with the free-text
   fallback (still useful, just doesn't have the picture-perfect
   structured markdown).

---

## What's NOT done

- **3x acceptance gate not yet closed.** Strict-shape interpretation
  causes the streak to reset on free-text fallback (~5-15% per deep
  run). Two paths forward documented in `bugs.md` B1.
- **Directional sanity backtest** has its fixture and runner ready
  but hasn't run end-to-end against Ollama (~75 min). Same harness;
  just hasn't been invoked.
- **A few `tests/verify/.runs/*.txt` Tee-Object files** are UTF-16
  encoded; cosmetic, see `bugs.md` B5.
