# Steps to Production — TradingAgents (Ollama-on-Windows path)

Single source of truth for getting TradingAgents running end-to-end on **this machine**. Supersedes `kyle.md` and `kyle-config.md`. Steps are **in order** — each step's "Blocks" line says what cannot start until it's done.

## Locked configuration

- **LLM:** Ollama (local), models already sized via llmfit. **No cloud API.** Anthropic via Claude Code + bun wrapper is for development assistance only — TradingAgents needs a raw `ANTHROPIC_API_KEY` we do not have.
- **Framework runtime:** Windows host + `uv`-managed venv.
- **Containers:** Docker is installed **inside WSL2** (Ubuntu-24.04, Docker 29.4.2). Not Docker Desktop. Only used for the optional Docker path.

## Environment snapshot (2026-05-10)

| Component | Status |
|---|---|
| `uv 0.11.8`, `python 3.14.4`, `git 2.54` | ✅ on host |
| `.venv/` | ✅ exists, imports `tradingagents` cleanly |
| Upstream | ✅ pulled to `19d22b5` (MiniMax-provider tip), 7 commits ahead of last local state |
| `uv.lock` local divergence | ⏸ stashed as `stash@{0}` ("local uv.lock upgrades (aiosqlite, anthropic 0.97)"); drop with `git stash drop` once you're sure |
| `~/.tradingagents/{cache,logs}/` | ✅ pre-existing |
| Ollama models pulled | ✅ see Phase 0 table |
| WSL2 `Ubuntu-24.04` w/ Docker 29.4.2 | ✅ installed, currently **Stopped** between sessions |
| `.env` | ❌ holds Azure-enterprise template (irrelevant) — leave empty for Ollama path |

## Pulled Ollama models (the real catalog for this machine)

| Tag | Size | Use |
|---|---|---|
| `qwen3-coder:30b` | 18 GB | **deep slot** — Research Manager / Trader / Portfolio Manager |
| `qwen3:8b` | 5.2 GB | **quick slot** — analyst loops, debaters, reflection LLM |
| `qwen2.5-coder:7b` | 4.7 GB | fallback quick model (older, kept around) |
| `qwen3-vl:8b` | 6.1 GB | vision — not used by this pipeline |
| `qwen2.5-coder:1.5b-base` | 986 MB | too small for this pipeline |
| `bge-m3:latest` | 1.2 GB | embeddings model — not a chat model |

**None of the CLI's default Ollama menu entries (`qwen3:latest`, `gpt-oss:latest`, `glm-4.7-flash:latest`) are pulled.** That's why we use the scripted `main.py` path below rather than the interactive CLI's provider prompt.

---

## Phase 0 — Verify prerequisites (already satisfied)

```powershell
uv --version            # 0.11.8
python --version        # 3.14.4
git --version           # 2.54
ollama list             # confirms the model table above
```

**Blocks:** everything below.

---

## Phase 1 — Sync the venv with the freshly pulled lockfile  ← START HERE

The pull already happened. `uv sync` reconciles `.venv/` with the new `uv.lock`.

```powershell
cd G:\Documents\GIT\RESEARCH-tools\TradingAgents
git status                           # expect "up to date with origin/main"
git log -1 --oneline                 # expect 19d22b5 ... MiniMax
uv sync
uv run python -c "from tradingagents.graph.trading_graph import TradingAgentsGraph; print('OK')"
```

Optional cleanup: `git stash drop` to discard the saved local lockfile divergence.

**Blocks:** Phase 2 (Ollama warmup uses the venv only for the import check) and Phases 3–6.

---

## Phase 2 — Verify Ollama is up and warm the chosen models

**Ollama is already running as a per-user autostart tray app** (`%LOCALAPPDATA%\Programs\Ollama\ollama app.exe`, launched at login), not a Windows service. The tray app spawns the server (`ollama.exe` on `localhost:11434`). Nothing to install or start manually.

`OLLAMA_CONTEXT_LENGTH=16384` is **persistently set** in the user environment, so the server inherits it on every restart. Verify:

```powershell
[Environment]::GetEnvironmentVariable('OLLAMA_CONTEXT_LENGTH','User')   # should print 16384
curl http://localhost:11434/api/version                                  # should print {"version":"..."}
curl http://localhost:11434/api/tags                                     # should list 6 models
```

If you ever change `OLLAMA_CONTEXT_LENGTH` and need the server to pick it up immediately (without waiting for a reboot):

```powershell
Get-Process -Name 'ollama*' | Stop-Process -Force
Start-Process -FilePath "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
```

Pre-load the models so first-token latency on the first agent isn't 60+ seconds:

```powershell
ollama run qwen3-coder:30b "ok" --keepalive 30m   # warm deep slot
ollama run qwen3:8b "ok" --keepalive 30m          # warm quick slot
ollama ps                                          # should show both, CONTEXT column ≥ 16384
```

`--keepalive 30m` keeps the models resident in VRAM/RAM between agent calls so we don't pay the load cost between every node. Increase if your runs span longer than 30 minutes.

**Blocks:** Phase 4 (smoke run cannot start without a listening server).

---

## Phase 3 — Configure `main.py` for Ollama (already done)

`main.py` has been edited in place. The config block now reads:

```python
config["llm_provider"]            = "ollama"
config["backend_url"]             = "http://localhost:11434/v1"
config["deep_think_llm"]          = "qwen3-coder:30b"
config["quick_think_llm"]         = "qwen3:8b"
config["max_debate_rounds"]       = 1
config["max_risk_discuss_rounds"] = 1
```

A header comment in the file flags this as a local override and points to the stash-on-pull workflow below.

### Workflow when upstream pushes a `main.py` change

Edit in place; before every `git pull`, stash this file, pull, then pop. Same pattern we used for `uv.lock`:

```powershell
git stash push main.py -m "local ollama config"
git pull --ff-only origin main
git stash pop                  # if conflict: edit manually, keep both upstream's diff and our config block
```

If a conflict gets too messy, the entire config block above is 6 lines — easy to rewrite from this doc and `git checkout main.py` to abandon the stash. The only state lost in that case is upstream changes to the surrounding scaffolding we don't care about.

**Blocks:** Phase 4A (`python main.py` smoke).

### If structured-output schema errors hit Research Manager / Trader / Portfolio Manager
Symptom: malformed JSON, missing `FINAL TRANSACTION PROPOSAL`, agent loops without producing a verdict. Try, in order:
1. Keep `qwen3-coder:30b` deep, swap quick to `qwen2.5-coder:7b`.
2. Both slots = `qwen3-coder:30b` (slower, but homogeneous and most reliable).
3. As a debugging crutch, set both to `qwen3:8b` to confirm whether the failure is provider-shape vs. model-quality (8B fails more visibly and faster).

### If an analyst loops forever calling tools (or never calls them)
Tool-calling fluency varies. Swap that slot's model to `qwen3:8b` — it's a chat-tuned model with better OpenAI-tool-shape adherence than `qwen3-coder:*` for some prompts.

---

## Phase 4 — Smoke-test end-to-end

### 4A — Scripted run (uses your `main.py` exactly)

```powershell
uv run python main.py
```

Expect: progress logs from market → social → news → fundamentals analysts (whichever subset you kept), then Bull/Bear debate, Research Manager verdict, Trader, three risk debaters, Portfolio Manager, and a printed final decision. Artefacts:

- `~/.tradingagents/logs/<TICKER>/<date>/reports/*.md` — per-agent prose
- `~/.tradingagents/memory/trading_memory.md` — appended **pending** decision entry

To prove the memory log resolution loop, re-run **the same ticker** on a **later date**: pending entries from prior runs get resolved (yfinance 5-day return vs SPY), a one-paragraph reflection is generated by the quick model, and the entries flip to resolved.

### 4B — Interactive CLI (only useful if you accept its model menu limitations)

```powershell
uv run tradingagents analyze
```

The CLI's Ollama menu won't show `qwen3-coder:30b` or `qwen3:8b`. You can pick any Ollama option to satisfy the prompt and then override in code — but for this machine it's simpler to skip the CLI and use 4A.

**Blocks:** Phase 5 (production use).

### Common failure modes (Ollama path)

| Symptom | Likely cause | Fix |
|---|---|---|
| Connection refused on `localhost:11434` | `ollama serve` window closed | Restart Phase 2 |
| Agent prose truncates mid-sentence | hit 4k default ctx | Phase 2: confirm `OLLAMA_CONTEXT_LENGTH=16384` was set **before** `ollama serve` |
| First call to each model takes minutes | model cold-loaded per call | Use `--keepalive 30m` in Phase 2 warmup |
| `FINAL TRANSACTION PROPOSAL` missing | structured-output JSON rejected | Phase 3 escalation ladder |
| Ticker truncated (e.g. `000404.SH` → `000404`) | shouldn't happen post-pull (e2c850e) | Confirm `git log -1 --oneline` shows `19d22b5` |
| `cp1252` UnicodeDecodeError | shouldn't happen post-v0.2.4 | Open() call missing `encoding="utf-8"` — file as regression |

---

## Phase 5 — Production use

Once Phase 4A returns a clean decision, "production" here means doing it for real tickers/dates with crash recovery on. Two flags you'll want:

```powershell
uv run tradingagents analyze --checkpoint           # save state after each LangGraph node
uv run tradingagents analyze --clear-checkpoints    # nuke per-ticker checkpoint DBs before run
```

Per-ticker SQLite checkpoints live at `~/.tradingagents/cache/checkpoints/<TICKER>.db`. Successful runs clear their own checkpoint; only crashes leave one behind. Same ticker + same date resumes; new date starts fresh.

Override artefact locations if `~/.tradingagents/` isn't where you want them:
```powershell
$env:TRADINGAGENTS_RESULTS_DIR     = "G:\trading\logs"
$env:TRADINGAGENTS_CACHE_DIR       = "G:\trading\cache"
$env:TRADINGAGENTS_MEMORY_LOG_PATH = "G:\trading\memory\trading_memory.md"
```

**Blocks:** nothing — terminal state.

---

## Phase 6 — (Optional) Dev tooling

Runtime venv has no `pytest`. Install if you want the suite:

```powershell
uv pip install pytest pytest-asyncio
uv run python -m pytest -m unit -q
```

`conftest.py` injects placeholder API keys, so unit tests pass with an empty `.env`. **Do not run `scripts/smoke_structured_output.py`** — it hits real cloud provider endpoints we don't have keys for.

**Blocks:** nothing.

---

## Phase 7 — (Optional) Docker via WSL2

Docker is in WSL, not on the Windows host, and `Ubuntu-24.04` is `Stopped` between sessions. This phase is only needed if you want to run the framework containerised; the Phase 1–5 path doesn't need any of this.

### 7A — Bring WSL up and verify

```powershell
wsl -d Ubuntu-24.04 -- docker version
wsl -d Ubuntu-24.04 -- docker compose version
```

### 7B — Keep WSL alive without an interactive shell (jury-rig)

WSL2 shuts a distro down ~8 seconds after the last process exits. Three options, easiest first:

**Option 1 — Heartbeat process (per-session):**
```powershell
Start-Process wsl -ArgumentList "-d","Ubuntu-24.04","--exec","sleep","infinity" -WindowStyle Hidden
```
Survives until reboot or `wsl --shutdown`. Re-run after each reboot.

**Option 2 — Logon autostart (persists across reboots):** Save a `.ps1` with the Option 1 command and register it as a Task Scheduler entry trigger="At log on of user", action="Start a program: powershell.exe -WindowStyle Hidden -File <path>". Use `schtasks /create` from an elevated shell:
```powershell
$cmd = 'Start-Process wsl -ArgumentList "-d","Ubuntu-24.04","--exec","sleep","infinity" -WindowStyle Hidden'
$ps  = "powershell.exe -WindowStyle Hidden -Command `"$cmd`""
schtasks /Create /SC ONLOGON /TN "WSL keep-alive" /TR $ps /RL HIGHEST /F
```

**Option 3 — Make Docker start at WSL boot (one-time, inside WSL):** edit `/etc/wsl.conf`:
```ini
[boot]
systemd=true
command="service docker start"
```
Then `wsl --shutdown` once from Windows so the next launch picks it up. Pair with Option 1 or 2 so the distro stays running long enough for the container to be reachable.

### 7C — Run the container

The repo lives at `G:\Documents\GIT\RESEARCH-tools\TradingAgents` on the host, which is `/mnt/g/Documents/GIT/RESEARCH-tools/TradingAgents` from inside WSL. Bind-mounts from `/mnt/...` are slower than native ext4 — fine for occasional use, costly for hot reload loops.

```powershell
wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/g/Documents/GIT/RESEARCH-tools/TradingAgents && docker compose --profile ollama run --rm tradingagents-ollama"
```

`--profile ollama` selects the local-models variant of `docker-compose.yml`. The Dockerfile already pre-creates `/home/appuser/.tradingagents` with correct ownership (704b762 from the just-pulled batch), so first-run named-volume mounts no longer PermissionError.

Note: the containerised Ollama profile expects an Ollama endpoint reachable from the container. If you keep `ollama serve` running on Windows (Phase 2), the container needs `host.docker.internal:11434` or equivalent — check `docker-compose.yml` and adjust `OLLAMA_HOST` / `backend_url` accordingly. **The host-side Phase 1–5 path avoids this entire problem;** prefer it unless you have a specific containerisation reason.

**Blocks:** nothing in this doc.

---

## Phase 8 — Iteration knobs

Cheapest first:
- `selected_analysts=["market", "news"]` — skip social + fundamentals (most token-heavy)
- `max_debate_rounds=1`, `max_risk_discuss_rounds=1` — already in Phase 3
- Both slots = `qwen3:8b` — fastest, lowest fidelity (debug only)
- Single ticker, recent date — most data already cached after first run

---

## Dependency graph

```
Phase 0 (tools) ──► Phase 1 (uv sync) ──┬──► Phase 2 (ollama serve + warm) ──► Phase 4 (smoke) ──► Phase 5 (prod)
                                        ├──► Phase 3 (main.py edit)         ──┘
                                        ├──► Phase 6 (pytest, optional)
                                        └──► Phase 7 (WSL Docker, optional, parallel)
```

Critical path: **0 → 1 → 2 → 3 → 4A → 5**. Everything else is optional.

---

## Untouched local files

These are not tracked upstream:

| File | Tracked? | Action |
|---|---|---|
| `CLAUDE.md` | gitignored ✅ | keep |
| `kyle.md` | not gitignored | superseded by this doc — delete or gitignore |
| `kyle-config.md` | not gitignored | superseded by this doc — delete or gitignore |
| `steps-to-production.md` | not gitignored | keep; gitignore if you don't want it tracked |

To gitignore them in one shot:
```powershell
Add-Content .gitignore "`nkyle*.md`nsteps-to-production.md"
```
