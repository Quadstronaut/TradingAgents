# Agent-assist guided menu + live progress — design

Date: 2026-05-11
Branch: feat/agent-assist
Status: approved by user, in implementation

## Problem

`agent.ps1` today takes a free-text prompt. Three pain points came out of a live session:

1. PowerShell ate `$` in `"If I had $100..."`, silently corrupting the prompt and producing a $5-budget screen with no candidates.
2. During the 15-minute deep run there is no terminal output — user sees a black screen and assumes the process hung.
3. Free-text prompts skip slots the orchestrator needs (budget, position context), forcing follow-up prompts that don't always fire.

## Goal

Replace the free-text entry with a flat numbered menu of common investment tasks. Each option collects only the slots it needs, then runs the appropriate flow with a live two-pane progress display.

Non-goals: parallel ticker runs, persistent portfolio store, building the Reddit scraper itself, touching the `tradingagents analyze` CLI.

## User flow

`./agent.ps1` with no args launches the menu. `-Prompt "..."` keeps working as a bypass for option 7 (free-form), so existing scripted usage doesn't break.

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

> 1
Ticker (e.g. NVDA, BRK.B): nvda
Will run deep analysis on NVDA, ~15 min. Proceed? [y/n]
```

Per-option slots:

| # | Task                       | Required slots                 | Optional slots |
|---|----------------------------|--------------------------------|----------------|
| 1 | Specific ticker            | ticker                         | —              |
| 2 | Theme screen               | theme (free text)              | budget         |
| 3 | Budget screen              | budget ($/share, int)          | theme          |
| 4 | Owned position             | ticker, shares, cost basis     | —              |
| 5 | Head-to-head               | ticker A, ticker B             | —              |
| 6 | News scan                  | ticker                         | —              |
| 7 | Free-form                  | prompt                         | budget         |

Slot collection uses Python `input()`, so PowerShell's `$` interpolation is bypassed.

Confirmation step before any LLM call: "Will run X on Y, ~N min. Proceed? [y/n]".

## Live progress display

Rich `Live` rendering with two views. ↑/↓ (or Tab) toggles; `q` silences to overview-only.

**Overview** — phase checklist with per-phase elapsed time and a spinner on the active phase:

```
NVDA — full deep analysis
  [✓] Market Analyst         (1:42)
  [✓] Social Analyst         (1:15)   ← Reddit sentiment if cached
  [✓] News Analyst           (2:08)
  [✓] Fundamentals Analyst   (1:51)
  [⠋] Bull vs Bear debate    (0:34…)
        └ round 1/1 in progress
  [ ] Trader · Risk debate · Portfolio Manager
elapsed 7:30  ·  est. remaining ~7 min  ·  ↑/↓ detail · q quiet
```

**Detail** — current phase header, streamed model tokens (last N lines), tool calls inline:

```
┌─ Bull vs Bear debate ─ round 1/1 ──────────────────────────
Bull: NVDA's data-center revenue grew 154% YoY in the latest
quarter. The Blackwell ramp is faster than Hopper was…
  · tool: get_finnhub_news (NVDA, last 7d)
Bear: But the multiple is now 35× forward earnings…
└─ elapsed 0:48 ─────────────────────────────────────────────
```

Both panes are driven by one event stream from `graph.stream(stream_mode=["values","messages"])`. A renderer thread redraws on a 4 Hz tick. A separate thread reads keystrokes (`msvcrt` on Windows, `termios` on POSIX) and flips `view_mode`.

Ctrl-C stops the Live, exits 130, no traceback. News-scan reuses the same display with fewer phases.

## New flow internals

**Compare (option 5)** — `agent_assist/compare.py`. Runs the existing deep pipeline on each ticker sequentially, then a final LLM step diffs both decision markdowns and produces a one-paragraph head-to-head with a recommendation. Summary file lists both ratings + the comparison.

**News scan (option 6)** — `agent_assist/news_scan.py`. A smaller LangGraph:

```
START → Social Analyst → News Analyst → Verdict → END
```

Verdict is a structured-output call:

```python
class NewsVerdict(BaseModel):
    lean: Literal["bullish", "neutral", "bearish"]
    confidence: float  # 0..1
    summary: str       # one paragraph
```

No Portfolio Manager, no Buy/Hold/Sell rating — output explicitly labelled "this is a read, not a recommendation."

**Reddit sentiment seam** — `agent_assist/reddit_sentiment.py` exposes a tool `get_reddit_sentiment(ticker, lookback_days=7)`. Registered in `agents/utils/agent_utils.py` and routed via `dataflows/interface.py`. The Social Analyst prompt is updated to call it when available.

### Reddit JSONL contract

Path: `~/.tradingagents/reddit_sentiment/<TICKER>.jsonl` (per-ticker, append-only).

Required fields per record:
- `ts` — ISO 8601 UTC timestamp
- `sub` — subreddit name (string)
- `score` — sentiment, -1.0 to 1.0
- `confidence` — 0.0 to 1.0
- `n_posts` — int, posts analysed

Optional:
- `theme` — short phrase (e.g. "AI demand still strong")
- `learning_version` — string, lets the scraper version its model
- `sample_quote` — one short quote

TradingAgents behavior:
- Reads last 7 days
- Weights themes by `confidence × log(n_posts)`
- Returns a structured summary to the analyst
- Missing/empty file → returns "no Reddit cache; using public sources" and the analyst proceeds with existing tools
- Malformed lines are skipped with a logger warning, not fatal

Documented in `docs/reddit_sentiment_contract.md` so the scraper project can target the contract independently.

## File layout

**New:**
- `tradingagents/agent_assist/menu.py` — flat picker + slot collection + Task model
- `tradingagents/agent_assist/progress.py` — Rich Live dual-pane + key listener
- `tradingagents/agent_assist/compare.py` — compare flow
- `tradingagents/agent_assist/news_scan.py` — stripped graph + verdict
- `tradingagents/agent_assist/reddit_sentiment.py` — JSONL reader + tool
- `docs/reddit_sentiment_contract.md` — contract doc for the scraper project
- `tests/agent_assist/test_menu.py`
- `tests/agent_assist/test_progress.py`
- `tests/agent_assist/test_reddit_sentiment.py`
- `tests/agent_assist/test_news_scan.py` (integration, skipped without Ollama)

**Modified:**
- `agent.ps1` — no-arg launches menu; keep `-Prompt` bypass; add `-Task` bypass for scripted use
- `scripts/agent_assist.py` — launches menu when neither `--prompt` nor `--task` is given
- `tradingagents/agent_assist/orchestrator.py` — accepts a `Task(intent, slots)` instead of a prompt string; clean KeyboardInterrupt
- `tradingagents/agents/utils/agent_utils.py` — register `get_reddit_sentiment`
- `tradingagents/dataflows/interface.py` — route `get_reddit_sentiment`

## Error handling

- KeyboardInterrupt → progress display tears down, summary not written, exit 130, no traceback
- Ollama unreachable → existing check in `scripts/agent_assist.py` stays
- Bad slot input → re-prompt the slot, don't drop back to the menu
- Reddit cache missing/malformed → degrade gracefully, never block
- LangGraph stream raises → progress display tears down, error surfaced to user, exit 1

## Testing

- `pytest -m unit` covers menu navigation, slot validation, KeyboardInterrupt handling, Reddit JSONL parsing (present, absent, malformed)
- `pytest -m integration` covers news-scan stripped graph (skipped without `OLLAMA_HOST` reachable)
- Progress display: fake a 3-event stream and assert both views render without errors. Key-listener thread mocked.

## Out of scope

- Parallel/concurrent ticker runs (compare option C was rejected)
- Persistent portfolio store across sessions
- The Reddit scraper project itself
- Replacing `tradingagents analyze` CLI
