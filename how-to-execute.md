# How to execute TradingAgents

Pragmatic, demo-tested invocation guide. Local-Ollama setup only.
For provider-cloud setups see `README.md`.

---

## Before you start (60 seconds)

```powershell
# 1) Ollama running?
ollama ps
# Expected: at least one model, with "<N> minutes from now" under UNTIL.
# If empty/error: launch the Ollama tray app, then:
#   ollama run qwen3:8b --keepalive 2h "ok"
#   ollama run qwen3-coder:30b --keepalive 2h "ok"

# 2) Required models pulled?
ollama list
# Need: qwen3:8b   AND   qwen3-coder:30b
# If missing:
#   ollama pull qwen3:8b
#   ollama pull qwen3-coder:30b

# 3) Optional: also make qwen3:latest resolvable (older CLI catalog used this id):
ollama cp qwen3:8b qwen3:latest
```

If `ollama ps` is empty, every entry point will fail with a `404 model not found`
or `connection refused`. Fix Ollama first.

---

## Three entry points — pick by what you want

| You want… | Use | Wall time | Output |
|---|---|---|---|
| Guided menu with 7 intents (news scan, deep, owned, theme, …) | `./agent.ps1` | varies | `~/.tradingagents/agent_assist/<ts>-<intent>.md` |
| Full questionary CLI with manual analyst / model selection | `uv run tradingagents` | 10-15 min deep | `~/.tradingagents/logs/<TICKER>/<DATE>/reports/*.md` |
| Scripted minimal NVDA deep run (smoke test) | `uv run python main.py` | 14-18 min | console only |

Both interactive paths produce the same end product (a Portfolio-Manager rating
+ markdown). `agent.ps1` is more demo-friendly because of the natural-language
prompts and the 5-min "news scan" intent. `uv run tradingagents` exposes
research depth, language, and per-analyst selection.

---

## Path 1 — `agent.ps1` (recommended for demos)

```powershell
.\agent.ps1
```

You'll see:

```
What would you like to do?

  1) Analyze a specific ticker
  2) Who's big in <theme> lately
  3) What can I do with $<N> this week
  4) Decide on a position I already own
  5) Compare two tickers head-to-head
  6) News & sentiment scan (no full debate, ~5 min)
  7) Write your own prompt (advanced)
  q) Quit

>
```

### Demo-friendly sequences

**Quickest visual (3-5 min):**
```
> 6
Ticker: NVDA
Run news scan on NVDA? [y]/n: y
```
Output: bullish/neutral/bearish read, no debate.

**Full deep pipeline (12-15 min, the "wow"):**
```
> 1
Ticker: NVDA
Run deep analysis on NVDA (~15 min)? [y]es / [s]kip / [a]bort: y
```
Output: 9-phase live progress display, ending with **Buy/Overweight/Hold/Underweight/Sell**.

**Free-form bypass (skip the menu):**
```powershell
.\agent.ps1 -Prompt 'should I buy NVDA'
.\agent.ps1 -Prompt 'tech under $100' -Budget 100
# Note: in PowerShell, single-quote prompts so "$N" doesn't get interpolated.
```

After it finishes:
- Summary markdown (now includes the full PM decision text):
  `C:\Users\<you>\.tradingagents\agent_assist\<timestamp>-<intent>.md`
- Full agent state (large JSON):
  `C:\Users\<you>\.tradingagents\logs\<TICKER>\TradingAgentsStrategy_logs\full_states_log_<date>.json`

---

## Path 2 — `uv run tradingagents` (manual selection)

```powershell
uv run tradingagents
```

The CLI is a single Typer command — there is **no `analyze` subcommand**. Just
`uv run tradingagents`. Optional flags:

```powershell
uv run tradingagents --checkpoint            # save state per node, resume on crash
uv run tradingagents --clear-checkpoints     # wipe and start fresh
```

### Questionary flow

| Step | Prompt | Demo answer |
|---|---|---|
| 1 | Ticker Symbol (default SPY) | **NVDA** |
| 2 | Analysis Date (default today) | press **Enter** |
| 3 | Output Language | **English** |
| 4 | Analysts Team (Space to toggle) | toggle all 4, then **Enter** |
| 5 | Research Depth | **Shallow** (~10 min) or **Medium** (~15 min) |
| 6 | LLM Provider | **Ollama** |
| 7 | Quick-Thinking LLM | **Qwen3 8B (local, fast)** |
| 8 | Deep-Thinking LLM | **Qwen3-Coder 30B (local, recommended)** |

The 30B deep model is the one that produces the right analysis. The 8B model
**will not** produce a coherent KTOS / NVDA report — it tends to hallucinate
about being a dataset-analysis chatbot. Always pick 30B for deep.

Reports written to `~/.tradingagents/logs/<TICKER>/<DATE>/reports/`:
- `market_report.md`, `sentiment_report.md`, `news_report.md`,
  `fundamentals_report.md`, `investment_plan.md`, `trader_investment_plan.md`,
  `final_trade_decision.md`.

---

## Path 3 — `python main.py` (one-shot smoke)

Smallest scripted run. NVDA, today's date, full pipeline. No prompts. Useful
when you just want to confirm the engine end-to-end:

```powershell
uv run python main.py
```

Edit `main.py` at the repo root to change ticker / date / models. Config is
pinned to `qwen3:8b` quick + `qwen3-coder:30b` deep.

---

## Verification harness (developer use)

```powershell
.\scripts\verify.ps1                    # one matrix pass across all 8 intents
.\scripts\verify.ps1 -Only news_scan    # single intent, fast
.\scripts\verify.ps1 -Quick             # skip the long deep intents
.\scripts\verify.ps1 -UntilGreen 3      # the 3x acceptance gate from spec
```

Outputs: `tests/verify/.runs/run-<timestamp>.{md,jsonl}`.

---

## If something goes wrong

### `NotFoundError: model 'qwen3:latest' not found` (or similar)
You picked a model the box doesn't have. Two fixes:
```powershell
# A: pull the missing one
ollama pull qwen3:8b
# B: alias an installed one under the requested name
ollama cp qwen3:8b qwen3:latest
```
Then re-run.

### `Ollama not reachable at http://localhost:11434`
Tray app crashed or never started. Launch Ollama from the Start menu and:
```powershell
ollama run qwen3:8b --keepalive 2h "ok"
```

### First analyst phase looks frozen for 30-90 s
Normal: Ollama is swapping models in/out of VRAM. Don't kill it. Subsequent
phases reuse the loaded model.

### Output is gibberish (model talks about being a chatbot, not the ticker)
You picked the 8B model for **deep** thinking. The 8B can't sustain the
analyst persona. Re-run with **Qwen3-Coder 30B** as the deep model.

### `tradingagents analyze` errors
Typer single-command quirk — `analyze` is the root, not a subcommand. Just
run `tradingagents` (no arg).

### `UnicodeEncodeError` during rich.Live teardown on Windows
Already worked around in `tests/verify/matrix.py` and the orchestrator (the
successful result is captured before teardown). If you see this from
`uv run tradingagents`, set `$env:PYTHONIOENCODING='utf-8'` before launch.

### Detail output missing from the agent-assist summary file
Was a real bug; fixed 2026-05-16. `summarize.write_summary` now embeds the
full Portfolio Manager decision text. The big JSON at
`~/.tradingagents/logs/<TICKER>/TradingAgentsStrategy_logs/full_states_log_<date>.json`
also has every analyst report under `market_report`, `sentiment_report`,
`news_report`, `fundamentals_report`, `investment_plan`,
`trader_investment_plan`, and `final_trade_decision`.

---

## Output locations cheat-sheet

```
~/.tradingagents/
├── agent_assist/                            ← agent.ps1 summary files
│   └── <ts>-<intent>.md                       (rating + PM decision markdown)
├── logs/
│   └── <TICKER>/
│       ├── <DATE>/                            ← uv run tradingagents path
│       │   ├── reports/*.md                     (per-analyst markdown)
│       │   └── message_tool.log
│       └── TradingAgentsStrategy_logs/        ← agent.ps1 path
│           └── full_states_log_<date>.json     (full state including all reports)
├── memory/
│   └── trading_memory.md                    ← decision log, auto-updated
└── cache/                                   ← yfinance / checkpoints
```

Override the base path with `$env:TRADINGAGENTS_CACHE_DIR`.
