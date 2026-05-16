# Stage 3 operator notes

Living notes for the 3x acceptance loop. Update on each iteration with
what failed, what was fixed, and how long it took.

## Pre-flight (must hold before any pass starts)

- [x] `ollama ps` shows `qwen3-coder:30b` and `qwen3:8b` loadable.
- [x] `uv run python -m pytest -m "not slow" -q` clean (412 pass + 1
  skipped Deepseek live).
- [x] `tests/verify/.runs/` exists and is writable.
- [x] `~/.tradingagents/agent_assist/verify/` exists and is writable.
- [x] News_scan green at least once (2026-05-15 23:32, ticker A, 211s).

## Per-attempt log

| # | Date | Pass # | Intents run | Green? | Failure mode | Fix applied |
|---|---|---|---|---|---|---|
| 1 | 2026-05-15 23:28 | 0 | news_scan | yes | — | — |
| 2 | 2026-05-15 23:?? | 0 | specific | (running) | TBD | — |

## When a pass goes red

1. Read `tests/verify/.runs/<latest>.md` for the failure row(s).
2. For each failure:
   - Identify `failure.intent`, `failure.error` and the inputs used
     (ticker, prompt, etc.).
   - **Don't dispatch a subagent blindly.** Read the per-agent reports
     under `~/.tradingagents/logs/<TICKER>/<date>/reports/` first — many
     failures are obvious from the analyst markdown.
3. If the failure needs investigation (~10+ tool calls), dispatch:
   - `Agent(subagent_type=general-purpose)` with the
     `superpowers:systematic-debugging` skill, scope = the file that
     raised, input = the traceback.
4. Apply the fix in the main session; rerun ONLY that intent before
   re-running the full matrix.

## Acceptance gate

```
./scripts/verify.ps1 -UntilGreen 3 -MaxAttempts 5
```

Three consecutive clean passes ⇒ done. Logs persisted under
`tests/verify/.runs/`.

## Open follow-ups (NOT blocking acceptance)

- progress.py rich.Live doesn't flush to non-TTY; output is silent until
  the deep run completes. Cosmetic. Possible fix: detect `not isatty()`
  in progress.py and degrade to plain-print mode.
- Memory log resolution loop is not in the matrix; a same-ticker rerun
  pair would prove it. Add as a Stage 2e follow-up if a reliable repro
  matters.
- Checkpoint resume on crash is not in the matrix; would need a
  controlled kill-and-resume harness. Out of scope per spec.
