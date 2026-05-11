# Agent-assist script — design

**Date:** 2026-05-10
**Status:** Draft for review
**Owner:** Quadstronaut

## Problem

Today, kicking off a TradingAgents run requires either editing `main.py` (hardcoded ticker/date) or sitting through ~10 interactive prompts in `tradingagents analyze`. Neither path lets you ask a natural-language question like:

- "What tech companies can I get into with $100 this week?"
- "I've got $50 today. What should I buy?"
- "Should I sell NVDA or wait?"

The framework also has no built-in **screening** capability — it takes a single ticker as input. Open-ended prompts need a candidate-discovery step before any analysis can run.

## Goal

A PowerShell entry point (`agent.ps1`) that accepts a free-form prompt and orchestrates:

1. **Screening** (only when needed) — turn a NL prompt into 2-3 candidate tickers
2. **Position context** (only when relevant) — ask once if you currently hold the named ticker
3. **Deep analysis** — run the existing TradingAgents pipeline on every candidate
4. **Aggregation** — present a ranked summary

The user stays in control of cost (Enter-to-continue gate) but doesn't have to micromanage which tickers to dig into.

## Non-goals

- No web UI. PowerShell + console only.
- No persistent watchlist or portfolio tracker. Each run is stateless except for the framework's existing memory log at `~/.tradingagents/memory/trading_memory.md`.
- No auto-open of report files. Paths are printed; the user opens what they want.
- No new LLM provider integrations. Reuses the existing local Ollama setup configured in `main.py`.
- No changes to the interactive `tradingagents analyze` CLI.

## Architecture

```
agent.ps1                          ← user entry point
   │
   │  uv run python scripts/agent_assist.py "<prompt>"
   ▼
scripts/agent_assist.py            ← orchestrator
   │
   ├─ A. parse_prompt(prompt)
   │     → intent: "screen" | "single" | "multi"
   │     → tickers: []  or  [explicit ticker(s)]
   │     → flags: hold_intent (bool)
   │
   ├─ B. shortlist(prompt, budget)  ← only if intent == "screen"
   │     → load scripts/data/ticker_universe.csv
   │     → if budget is None: ask user "Max price per share? ($, Enter to skip)"
   │     → call Ollama (qwen3:8b) with structured output
   │       → returns 2-3 candidates with reasoning
   │     → enrich each with current price via yfinance
   │     → drop any that yfinance can't price OR exceeds budget
   │     → re-prompt Ollama once if survivors < 2 (cap at one re-prompt)
   │
   ├─ C. show_shortlist(candidates)
   │     → print full shortlist with prices + reasoning + total est. time
   │     → no gate yet — that's per-ticker in step E
   │
   ├─ D. ask_position(ticker)      ← only if hold_intent or len(tickers)==1
   │     → "do you hold this? share count + cost basis (Enter to skip)"
   │     → returns string  or  None
   │
   ├─ E. for each ticker in candidates:
   │     → per-ticker confirm: "Run deep analysis on {TICKER} (~15 min)? [y]es / [s]kip / [a]bort"
   │       y → propagate; s → mark SKIPPED, continue to next; a → break loop
   │     → TradingAgentsGraph(...).propagate(
   │           ticker, today, additional_portfolio_context=position_str
   │       )
   │     → collect (ticker, rating, log_path)
   │     → on exception: log error, continue with the rest
   │
   └─ F. summarize(results)
        → print ranked table (Buy > Overweight > Hold > Underweight > Sell)
        → write ~/.tradingagents/agent_assist/<ts>-<slug>.md
        → print path
```

## Components

### 1. `agent.ps1` (new, repo root)

Tiny PowerShell wrapper. Parameters:
- `[Parameter(Mandatory)][string]$Prompt` — the natural-language ask
- `[int]$Budget` — optional max per-share price in USD; if omitted, the Python helper will prompt for it interactively whenever it's about to do a screen

Validates the prompt is non-empty, cd's to the repo root, runs:

```powershell
uv run python scripts/agent_assist.py --prompt $Prompt @(if ($Budget) { '--budget'; $Budget })
```

Exit code propagates. On Ollama-not-running errors (Python helper exits with code 2), prints the diagnostic from `quickstart.md`:

```
curl http://localhost:11434/api/version
```

That's it — no business logic in PS1.

### 2. `scripts/agent_assist.py` (new)

Python orchestrator. Single `main(prompt: str) -> int` entry. Imports `TradingAgentsGraph` and uses the same Ollama config as `main.py` (`qwen3-coder:30b` deep, `qwen3:8b` quick).

Sub-modules under `scripts/agent_assist/`:

- `prompt_parse.py` — `parse_prompt(text) -> ParsedPrompt`. Detects:
  - **Tickers**: regex `\b[A-Z]{1,5}(?:\.[A-Z]{1,2})?\b` cross-checked against the universe CSV (rejects words like "USA", "ETF" that match the regex but aren't tickers; accepts exchange-qualified forms like `7203.T`, `BRK.B`).
  - **Hold intent**: keywords `sell`, `wait`, `hold`, `dump`, `keep`, `cut`, `trim`, `add to`.
  - **Intent**: `single` if exactly one ticker found, `multi` if 2+, `screen` otherwise.
- `shortlist.py` — `shortlist(prompt, universe, budget) -> list[Candidate]`. Calls Ollama with a JSON-mode response_format and a Pydantic schema:
  ```python
  class Candidate(BaseModel):
      ticker: str
      reasoning: str  # one sentence, why this fits the prompt

  class ShortList(BaseModel):
      candidates: list[Candidate]  # 2-3 items
  ```
  Universe is passed in as a compact `ticker | name | sector` table. The LLM handles **qualitative** filters (sector, theme, "boring" vs "momentum") since it sees the prompt verbatim. The script handles the **price ≤ budget** filter using the explicit `budget` argument — **no regex parsing of the prompt for dollar amounts**. The flow:
  - If the caller passed `--budget N`, use that.
  - Otherwise, before calling Ollama, ask the user explicitly: `Max price per share? ($, Enter to skip)`. Empty input means no price cap.
  - After Ollama returns the shortlist, yfinance enriches each `Candidate` with `last_price` (via `yf.Ticker(t).fast_info["lastPrice"]`).
  - yfinance failure → drop the candidate
  - price > budget (when budget is set) → drop the candidate
  - if shortlist drops below 2 after filtering, re-prompt Ollama once with the surviving candidates listed as "already considered, pick different ones" plus the explicit budget cap; cap at one re-prompt
  - if budget is `None`, the price filter is skipped (only the yfinance-failure drop applies)

  Always-explicit budget keeps the user in control of dollar exposure — no risk of a sloppy regex letting through a $400 stock when they meant $40.
- `position.py` — `ask_position(ticker) -> str | None`. Two `input()` prompts (count + cost basis), formats into:
  ```
  User currently holds N shares of {ticker} bought at ${cost_basis} cost basis.
  ```
  Returns `None` if either input is empty.
- `summarize.py` — `summarize(results, output_dir) -> Path`. Renders a markdown summary file (results table + per-ticker rating + link to the framework's per-ticker log dir) and returns the file path.

### 3. `scripts/data/ticker_universe.csv` (new, bundled)

Static CSV. Columns: `ticker, name, sector, industry`. Source: S&P 500 constituents ∪ Nasdaq 100 constituents, deduplicated. ~600 rows. Generated once via `scripts/refresh_universe.py` (also new — small, not part of normal flow). Committed to the repo. Refresh is manual; staleness is acceptable since deep analysis still uses live yfinance data.

### 4. `scripts/refresh_universe.py` (new, manual-use only)

Standalone script that fetches current S&P 500 + Nasdaq 100 constituents (Wikipedia tables via pandas) and rewrites `scripts/data/ticker_universe.csv`. Not invoked by `agent_assist.py`. Run as needed — e.g., quarterly.

### 5. Framework change: `additional_portfolio_context`

One small change inside `tradingagents/`:

- `tradingagents/graph/propagation.py` — `Propagator.create_initial_state` gains `additional_portfolio_context: str = ""` kwarg, threaded into the returned state dict alongside `past_context`.
- `tradingagents/graph/trading_graph.py` — `TradingAgentsGraph.propagate(company_name, trade_date)` gains `additional_portfolio_context: str = ""` kwarg, forwarded to `create_initial_state`.
- `tradingagents/agents/managers/portfolio_manager.py` — reads `state.get("additional_portfolio_context", "")` and injects into the prompt **after** the lessons block, formatted as:
  ```
  - User-provided position context:
  {additional_portfolio_context}
  ```
  Empty string → injects nothing (current behavior preserved).

This is a strictly additive API change. All existing callers (CLI, `main.py`, tests) continue to work unchanged.

## Data flow examples

### Example 1: `agent.ps1 "tech companies I can get into this week"` (no `-Budget` flag)

1. **parse_prompt** → intent=`screen`, tickers=[], hold_intent=False
2. **shortlist** →
   - No `--budget` passed, so prompt: `Max price per share? ($, Enter to skip)` → user types `100`
   - Ollama picks AMD, INTC, PLTR; yfinance prices $142, $24, $87 → INTC and PLTR survive (≤ $100); AMD dropped
   - Survivors = 2, no re-prompt needed (threshold is < 2). Final shortlist: INTC, PLTR.
3. **show_shortlist** → prints the two with prices, reasoning, "≈ 30 min if you run both"
4. **ask_position** → skipped (hold_intent=False, multi-ticker)
5. **per-ticker loop**:
   - `Run deep analysis on INTC (~15 min)? [y/s/a]` → `y` → propagate → rating: Hold
   - `Run deep analysis on PLTR (~15 min)? [y/s/a]` → `s` → marked SKIPPED
6. **summarize** → table shows INTC=Hold, PLTR=SKIPPED + path to log dir + summary markdown file

### Example 2: `agent.ps1 "should I sell NVDA or wait"`

1. **parse_prompt** → intent=`single`, tickers=[NVDA], hold_intent=True
2. **shortlist** → skipped
3. **show_shortlist** → just prints "Single ticker: NVDA"
4. **ask_position** → `How many shares of NVDA?` `50`. `Cost basis ($/share)?` `130`. Position string built.
5. **per-ticker loop**:
   - `Run deep analysis on NVDA (~15 min)? [y/s/a]` → `y` → propagate with `additional_portfolio_context` → Portfolio Manager sees the position → rating: Overweight
6. **summarize** → single-row table + path to `~/.tradingagents/logs/NVDA/...`

### Example 3: `agent.ps1 -Budget 50 "what should I buy"` (open-ended with explicit budget)

1. **parse_prompt** → intent=`screen`, tickers=[], hold_intent=False
2. **shortlist** →
   - `--budget 50` passed, no interactive prompt
   - Ollama picks 3 names; yfinance filters anything > $50
3. ...as Example 1 from step 3 onward

## Error handling

| Failure | Behavior |
|---|---|
| Ollama not reachable | Exit 2 with the probe command from `quickstart.md`. PS1 surfaces it cleanly. |
| yfinance fails on a candidate during shortlist | Drop the candidate, continue. If shortlist drops to 0, exit 3 with "no priceable candidates — try a more specific prompt". |
| Ollama returns malformed JSON for the shortlist | Retry once with stricter prompt. If still bad, exit 4 with "shortlist model returned unparseable output". |
| Propagate raises during stage E for one ticker | Log to console, mark as `FAILED` in the summary, continue with the next ticker. |
| User answers `s` (skip) at per-ticker confirm | Mark as `SKIPPED` in summary, continue to next ticker. |
| User answers `a` (abort) at per-ticker confirm | Break the loop, print partial summary for whatever already ran, exit 0. |
| Ctrl+C during a propagate run | Kill current run, print partial summary for completed tickers, exit 130. |
| Universe CSV missing or unreadable | Exit 5 with "run scripts/refresh_universe.py first". |
| Empty prompt | PS1 catches it before invoking Python; prints usage. |

## Testing

Mark all new tests `unit` unless otherwise noted. None should require live Ollama or network.

- `tests/test_agent_assist_prompt_parse.py` — table-driven tests for `parse_prompt`. Cases:
  - Single explicit ticker → `single`
  - Two explicit tickers → `multi`
  - Open-ended ("$50 to spend") → `screen`
  - Sell-intent with ticker → `single` + `hold_intent=True`
  - Exchange-qualified ticker (`7203.T`, `BRK.B`) → preserved verbatim
  - False-positive guards: "USA", "ETF", "BUY" not picked up as tickers
- `tests/test_agent_assist_shortlist.py` — mock `OpenAIClient` (Ollama uses the OpenAI-compatible path) and `yfinance.Ticker`. Verify:
  - Returns 2-3 candidates
  - yfinance failure drops the candidate
  - Empty result triggers retry
- `tests/test_agent_assist_summarize.py` — given fixture results, assert markdown file shape and ranking order.
- `tests/test_portfolio_manager_position_context.py` — verify the new `additional_portfolio_context` kwarg flows from `propagate` → state → prompt. Use `marker=integration` only if it actually invokes an LLM; otherwise mock the LLM and assert the prompt string contains the position text.

End-to-end smoke (manual, not in `pytest`): `agent.ps1 "should I buy AAPL"` against live Ollama. Expected: ~15 min run, ends with single-row summary.

## What's NOT in v1

- Multi-currency / non-USD price filtering (universe is US-only)
- Watchlist persistence
- Cron/scheduled runs
- Re-using a recent analysis if you ask the same question twice within N hours (memory log already records the prior verdict, but no caching layer here)
- Sector-themed prompts beyond what Ollama can infer from the universe CSV (no fancy taxonomy)

## File inventory

**New:**
- `agent.ps1`
- `scripts/agent_assist.py`
- `scripts/agent_assist/__init__.py`
- `scripts/agent_assist/prompt_parse.py`
- `scripts/agent_assist/shortlist.py`
- `scripts/agent_assist/position.py`
- `scripts/agent_assist/summarize.py`
- `scripts/refresh_universe.py`
- `scripts/data/ticker_universe.csv`
- `tests/test_agent_assist_prompt_parse.py`
- `tests/test_agent_assist_shortlist.py`
- `tests/test_agent_assist_summarize.py`
- `tests/test_portfolio_manager_position_context.py`
- `docs/superpowers/specs/2026-05-10-agent-assist-script-design.md` (this file)

**Modified:**
- `tradingagents/graph/propagation.py` — add `additional_portfolio_context` kwarg
- `tradingagents/graph/trading_graph.py` — add `additional_portfolio_context` kwarg to `propagate`
- `tradingagents/agents/managers/portfolio_manager.py` — read state, inject into prompt when non-empty
