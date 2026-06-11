# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

```bash
pip install -e .                        # editable install for development
pip install .                           # regular install (provides `tradingagents` script)

tradingagents                   # interactive CLI (prompts for ticker, date, provider, etc.)
tradingagents --checkpoint      # opt-in resume; saves state after each LangGraph node
tradingagents --clear-checkpoints  # delete all per-ticker checkpoint DBs first
python -m cli.main              # equivalent to `tradingagents`
python main.py                          # minimal scripted run (NVDA example) — useful for smoke checks

pytest                                  # full suite; auto-loads conftest with placeholder API keys
pytest tests/test_signal_processing.py::test_xxx -v
pytest -m unit                          # markers: unit / integration / smoke (declared in pyproject.toml)

python scripts/smoke_structured_output.py openai      # exercises the 3 structured-output agents
python scripts/smoke_structured_output.py anthropic   # against any supported provider — costs real tokens

docker compose run --rm tradingagents                 # containerised CLI; reads .env
docker compose --profile ollama run --rm tradingagents-ollama   # local-models variant
```

`test.py` at repo root is a one-off yfinance benchmark, not part of the pytest suite. Run with `python test.py` only when explicitly checking indicator-window timings.

## Architecture

TradingAgents is a **LangGraph state machine** of LLM-powered agents that mirrors a trading firm's workflow. Read `tradingagents/graph/setup.py` and `tradingagents/graph/trading_graph.py` together — the topology is non-obvious from any single file.

### Execution flow (`GraphSetup.setup_graph`)

```
START → [selected analysts in sequence] → Bull ↔ Bear (debate, N rounds)
      → Research Manager → Trader
      → Aggressive ↔ Conservative ↔ Neutral (risk debate)
      → Portfolio Manager → END
```

Analysts are chained dynamically based on `selected_analysts` (any subset of `market` / `social` / `news` / `fundamentals`). Each analyst loops with its own `ToolNode` until it stops calling tools, then a `Msg Clear` node wipes the message buffer (Anthropic compatibility) before the next analyst runs. Conditional edges live in `tradingagents/graph/conditional_logic.py`.

### Three structured-output agents

Research Manager, Trader, and Portfolio Manager use `llm.with_structured_output(Schema)` and return typed Pydantic instances defined in `tradingagents/agents/schemas.py`. Render helpers in the same module turn each instance back into the **exact markdown shape** (`**Recommendation**:`, `**Action**:`, `**Rating**:`, `FINAL TRANSACTION PROPOSAL: **...**`) that downstream consumers parse — memory log, CLI display, saved reports, and `SignalProcessor` all depend on those headers. **If you edit a schema, update the matching `render_*` so the markdown contract holds.** `tradingagents/agents/utils/structured.py` handles provider-specific binding (json_schema for OpenAI/xAI/DeepSeek/Qwen/GLM, response_schema for Gemini, tool-use for Anthropic, OpenAI-compatible function-calling fallback).

`SignalProcessor` (`tradingagents/graph/signal_processing.py`) extracts the final rating from the Portfolio Manager's rendered markdown via a deterministic heuristic — **no LLM call**. The 5-tier scale (Buy / Overweight / Hold / Underweight / Sell) is used by Research Manager, Portfolio Manager, signal processor, and the memory log; Trader keeps a 3-tier scale (Buy / Hold / Sell) because transaction direction is naturally ternary.

### LLM provider abstraction

`tradingagents/llm_clients/factory.py:create_llm_client` lazy-imports each provider client. OpenAI-compatible providers (`openai`, `xai`, `deepseek`, `qwen`, `glm`, `ollama`, `openrouter`) share `OpenAIClient`; `anthropic`, `google`, `azure` have dedicated clients. `tradingagents/llm_clients/model_catalog.py` is the **single source of truth** for CLI options and provider validation — keep it in sync when adding models. `validators.py` enforces the catalog at the client boundary.

`config["backend_url"]` defaults to `None` on purpose. Each client falls back to its provider's native default endpoint. Reintroducing a non-None default leaks (e.g.) the OpenAI URL into Gemini and produces malformed request URLs — the v0.2.4 fix specifically removed that.

### Data vendor routing

Tools in `tradingagents/agents/utils/agent_utils.py` are abstract; the actual fetcher is selected per-call by `tradingagents/dataflows/interface.py` based on `config["data_vendors"]` (category-level) and `config["tool_vendors"]` (per-tool override). Two vendors today: `yfinance` (default, no key) and `alpha_vantage` (requires `ALPHA_VANTAGE_API_KEY`). yfinance fetchers retry with exponential backoff. Backtest fetchers must not leak look-ahead data when `curr_date` falls inside a fetched window — the date-fidelity fix in v0.2.3 closed this; preserve it.

### Persistence

Two distinct stores under `~/.tradingagents/` (override base with `TRADINGAGENTS_CACHE_DIR`):

- **Decision log** (`memory/trading_memory.md`, override with `TRADINGAGENTS_MEMORY_LOG_PATH`) — append-only markdown. `TradingMemoryLog.store_decision()` writes a `pending` entry at the end of every `propagate()`. On the **next same-ticker run**, `_resolve_pending_entries()` fetches `yf.Ticker(ticker).history()` for that ticker and SPY, computes raw + alpha return over a 5-day default holding window, generates a one-paragraph reflection via the quick LLM, then atomically batch-updates resolved entries. Only the Portfolio Manager consults memory, and only when entries exist (the empty-memory hallucination from #572 is structurally impossible). `memory_log_max_entries` caps **resolved** entries; pending entries are never pruned.
- **Checkpoints** (`cache/checkpoints/<TICKER>.db`) — opt-in via `config["checkpoint_enabled"]` or `--checkpoint`. Per-ticker SQLite avoids cross-ticker contention. `thread_id(ticker, date)` is a SHA-256 prefix so the same ticker+date resumes but a new date starts fresh. Successful runs clear their own checkpoint. The graph is recompiled with the `SqliteSaver` only when the flag is set; the default compiled graph has no checkpointer.

### Path safety

**Anything user-provided or LLM-provided that ends up in a filesystem path must go through `tradingagents.dataflows.utils.safe_ticker_component`** — tickers reach paths from CLI input AND from agent tool calls (which can be influenced by prompt injection in fetched news). It rejects `..`, dots-only, oversize, and anything outside `[A-Za-z0-9._\-\^]`. Used today by `_log_state` (results dir) and `_db_path` (checkpoint dir). Don't bypass it when adding a new file path that includes a ticker.

### Encoding

All `open()` calls pass explicit `encoding="utf-8"`. Windows defaults to cp1252 and silently mangles non-ASCII content otherwise. The v0.2.2 attempt at a process-level UTF-8 default did not actually take effect — v0.2.4 replaced it with explicit per-call encoding. Keep this discipline when adding file I/O.

### Output language

`config["output_language"]` (default `"English"`) only affects user-facing analyst reports and the final decision (via `get_language_instruction()` injected into prompts). **Internal agent debate stays in English** for reasoning quality — don't propagate the language instruction into bull/bear/risk debaters.

## Project conventions

- The framework's primary artifact is **prose**. Structured output is layered onto the three decision agents only; analysts and debaters produce free-text. When adding agents, default to prose unless there's a concrete reason a downstream consumer needs typed fields.
- Risk debaters are named **Aggressive / Conservative / Neutral** in code and CLI display (renamed from Risky/Safe in v0.2.0). The `risk_manager` was renamed to **`portfolio_manager`** in v0.2.2. Use the current names.
- Exchange-qualified tickers (`7203.T`, `BRK.B`, `.HK`, `.L`, `.TO`) must round-trip through prompts and tool calls unchanged. `build_instrument_context()` in `agent_utils.py` enforces this in prompts.
- `pytest` markers `unit`, `integration`, `smoke` are declared in `pyproject.toml`; new tests should pick one. `conftest.py` autouse-injects `placeholder` API keys so tests run cleanly without credentials — don't add tests that hit real endpoints under the `unit` marker.
- This project is research code with a strict disclaimer (see README and `tauric.ai/disclaimer/`). It is not financial advice.
