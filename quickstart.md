# Quickstart — TradingAgents (local Ollama)

For the full reasoning behind each step and troubleshooting, see [`steps-to-production.md`](./steps-to-production.md).

## One-time setup (already done on this machine)

- `uv` venv built at `.venv/` — `uv sync` to refresh after `git pull`.
- `main.py` configured for Ollama: deep=`qwen3-coder:30b`, quick=`qwen3:8b`.
- `OLLAMA_CONTEXT_LENGTH=16384`, `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_NUM_PARALLEL=1` set in user env.
- Ollama tray app autostarts at login. Both target models pulled.

## Run an analysis

Change ticker/date inside `main.py` (lines near the bottom), then:

```powershell
cd G:\Documents\GIT\RESEARCH-tools\TradingAgents
uv run python main.py
```

That's it. Single-word verdict prints to stdout. Wall time ~10–15 min per run (GPU forces model swaps between deep/quick agents).

## Outputs

| Where | What |
|---|---|
| stdout | One word: `Buy` / `Overweight` / `Hold` / `Underweight` / `Sell` |
| `~/.tradingagents/memory/trading_memory.md` | Appended pending decision with rating, thesis, price target, stop, horizon |
| `~/.tradingagents/logs/<TICKER>/TradingAgentsStrategy_logs/full_states_log_<date>.json` | Full graph state JSON |
| `~/.tradingagents/cache/<TICKER>-YFin-data-*.csv` | yfinance price cache |

On the **next same-ticker run**, the pending entry from before gets auto-resolved (yfinance fetches realised return vs SPY, quick LLM writes a reflection) before the new analysis starts.

## Want per-agent markdown reports?

Use the interactive CLI instead — it writes `reports/<date>/*.md` per agent on top of everything above:

```powershell
uv run tradingagents analyze            # interactive prompts
uv run tradingagents analyze --checkpoint   # crash-resumable per-node state
```

CLI's Ollama model menu won't list `qwen3-coder:30b` / `qwen3:8b` (they're not in the upstream catalog) — pick any Ollama option to get past the prompt; model IDs are still controlled by `main.py` config when launched programmatically, or by the CLI's own picker for the interactive case. For best quality on the CLI path, just edit `cli/utils.py:MODEL_OPTIONS` to add your models, or run `python main.py` directly.

## Resetting state

```powershell
# Wipe checkpoint DBs (per-ticker SQLite) before a run
uv run tradingagents analyze --clear-checkpoints

# Wipe the memory log
Remove-Item ~\.tradingagents\memory\trading_memory.md

# Wipe all logs/cache
Remove-Item -Recurse -Force ~\.tradingagents\logs, ~\.tradingagents\cache
```

## Verifying the env before a run (60-second sanity check)

```powershell
curl http://localhost:11434/api/version          # Ollama up?
ollama list | findstr "qwen3"                    # models pulled?
git status                                        # main.py edited, nothing surprising?
git log -1 --oneline                              # know what version you're on
```

If Ollama isn't listening: relaunch the tray.

```powershell
Start-Process "$env:LOCANAPPDATA\Programs\Ollama\ollama app.exe"
```

(Typo guard: that variable is `LOCALAPPDATA`, not `LOCANAPPDATA` — fix if you copy-paste.)

## Pulling upstream changes

```powershell
git stash push main.py -m "local ollama config"
git pull --ff-only origin main
git stash pop                  # resolve manually if upstream touched main.py
uv sync
```

If `git stash pop` conflicts: the local config block is 6 lines (deep model, quick model, provider, backend_url, two debate-round caps). Easy to re-apply by hand or just `git checkout main.py` and re-edit using [`steps-to-production.md`](./steps-to-production.md) Phase 3.

## Switching to a different ticker / date

Edit the last few lines of `main.py`:

```python
_, decision = ta.propagate("AAPL", "2026-04-15")     # ticker, analysis date
```

Exchange-qualified tickers round-trip cleanly: `7203.T`, `BRK.B`, `0700.HK`, `RIO.L`, `RY.TO`.

## Natural-language wrapper (`agent.ps1`)

For prompts you'd actually phrase out loud:

```powershell
./agent.ps1 "should I buy NVDA"
./agent.ps1 "tech companies I can get into this week" -Budget 100
./agent.ps1 "should I sell NVDA or wait"
```

What it does:

- **Single ticker** (`should I buy NVDA`) → goes straight to the full pipeline.
- **Hold/sell intent** OR **single ticker** → asks once for share count + cost basis (Enter to skip). Position info is injected into the Portfolio Manager prompt.
- **Open-ended** (`tech under $100`, `what should I buy`) → uses local Ollama (qwen3:8b) + a bundled S&P 500 ∪ Nasdaq 100 universe to shortlist 2-3 candidates. If you didn't pass `-Budget`, it asks for the cap interactively. Then enforces it with live yfinance prices.
- For each shortlisted ticker → asks `[y]es / [s]kip / [a]bort` before each ~15-min run.
- After all runs → ranked summary written to `~/.tradingagents/agent_assist/<timestamp>-<slug>.md`.

Refresh the universe (run quarterly or after Wikipedia layout changes):

```powershell
uv run python scripts/refresh_universe.py
```

If Ollama isn't running, the script exits with code 2 and the diagnostic command. See the troubleshooting section above.
