# TradingAgents full rectification — design

Date: 2026-05-15
Branch: feat/agent-assist → child branch `verify/full-battery`
Status: approved by user, ready for implementation plan

## Problem

The `feat/agent-assist` branch has shipped seven user-facing intents (specific, theme, budget, owned, compare, news_scan, freeform) and 22 unit-test files, all using mocked LLM clients. There is **no live end-to-end verification** that:

- a real Ollama-backed run produces a valid rating without crashing,
- the structured-output contract (Pydantic + `FINAL TRANSACTION PROPOSAL: **...**` markdown) survives real model output,
- the 5-tier rating direction is correlated with realised forward returns even on obvious-signal historical fixtures,
- the cloud LLM clients we ship (Anthropic, OpenAI, Azure, Google, xAI, DeepSeek, Qwen-cloud, GLM, OpenRouter) and the AlphaVantage data-vendor path still match their providers' wire shapes,
- the system survives three consecutive runs without a crash, malformed response, or aborted run.

This spec drives the project to the state where `make verify-3x` exits clean (zero failures) three times in a row, covering every intent and every shipped surface.

## Goal

Build a verification harness and the tests it runs, then drive the codebase to "three clean passes" using a phased reactive multi-agent pipeline. Each pass must clear three bars:

1. **Robustness** — exit code 0, rating in `{Buy, Overweight, Hold, Underweight, Sell}`, no uncaught exception.
2. **Shape** — Pydantic instances validate; rendered markdown contains the required headers; `render_*` round-trips bytes-equal to the model markdown.
3. **Directional sanity** — on 5 labelled historical fixtures, ≥4 ratings match the realised 5-day-alpha direction (Buy/Overweight ↔ positive, Sell/Underweight ↔ negative, Hold ↔ near-zero).

Non-goals: changing the locked Ollama configuration; rewriting the LangGraph topology; adding new analyst types; touching the upstream-tracked `main.py` config block.

## User-visible answers locked in during brainstorming

| Question | Answer |
|---|---|
| Pass criteria | Robustness + shape + directional sanity |
| Time budget | No limit — take whatever it takes |
| Cloud surfaces (no keys) | VCR-style recorded cassettes for contract tests |
| Randomness | Seeded, rotating across passes; curated vague-prompt pool |
| Pipeline shape | Approach C — phased reactive multi-agent |

## Branch / workspace strategy

- Create a git worktree at `..\TradingAgents-verify\` on a new branch `verify/full-battery` cut from `feat/agent-assist`.
- Untracked admin files in the current working tree (`CLAUDE.md`, `kyle*.md`, `steps-to-production.md`, `scripts/expand_universe.py`) stay behind — the worktree starts with a clean working tree from the branch.
- Ollama (host-side) and `~/.tradingagents/` (host-side) are shared with the worktree automatically.
- Merge strategy: after three clean passes, open a PR from `verify/full-battery` into `feat/agent-assist`. Squash-or-rebase per repo convention; the worktree is removed by `git worktree remove` once the PR lands.

## Architecture

Four stages, gated. Subagents are dispatched only inside Stage 3, and only on red.

```
Stage 0 — Audit (main, solo)
  Produce a per-file gap matrix: existing tests, holes, observed risks.
  Decide order in which intents will be hardened (cheapest first).

Stage 1 — Harness build (main + 1-2 helpers as needed)
  Build the verification scaffolding itself, with its own unit tests.
  Helpers only for genuinely-independent VCR cassette generation.

Stage 2 — Test writing (main, with bursts of parallel coder subagents)
  Per-intent E2E tests, shape assertions, backtest cases, contract tests.
  Parallel coders only when the slice is file-isolated (e.g., distinct
  contract-test files per cloud client).

Stage 3 — 3x verification loop (main, reactive multi-agent)
  Run the matrix.
  On green: streak++; on three consecutive greens, exit.
  On red: per failure, spawn systematic-debugging → coder → reviewer.
  Abort budget: same root cause recurring 3x pauses the loop.
```

## The harness (Stage 1 deliverables)

All paths under `tests/verify/` unless stated. The harness has its own unit tests at `tests/verify/test_harness_*.py`.

### Fixtures

- **`tests/verify/fixtures/random_ticker.py`** — seeded picker from `tradingagents/agent_assist/data/ticker_universe.csv`. `pick(pass_no, *, step=7) -> str` uses `idx = (pass_no * step) % len(universe)`. Excludes any ticker that yfinance has flagged as unfetchable in a precomputed exclusion list at `tests/verify/fixtures/ticker_exclusions.txt` (populated by a one-shot scan in Stage 1).
- **`tests/verify/fixtures/vague_prompts.py`** — frozen list of ≥10 curated prompts (e.g., `"best AI plays this week"`, `"cheap biotech under $50"`, `"boring dividend stocks"`, `"renewable energy plays"`, `"semis with positive cash flow"`, `"defensive consumer staples"`, `"value picks under $30"`, `"new IPOs gaining traction"`, `"undervalued small caps"`, `"momentum names today"`). `pick(pass_no) -> str` uses the same rotating modulo scheme.
- **`tests/verify/fixtures/backtest_dataset.csv`** — five rows, columns: `ticker, analysis_date, realized_5d_alpha, expected_direction`. Picked from obvious-signal historical events (large earnings beats/misses) at least 30 days old so the 5-day window has fully realised. `expected_direction ∈ {positive, negative, neutral}`.
- **`tests/verify/cassettes/`** — VCR-Py cassette files per cloud provider (`anthropic.yaml`, `openai.yaml`, `google.yaml`, `azure.yaml`, `xai.yaml`, `deepseek.yaml`, `qwen.yaml`, `glm.yaml`, `openrouter.yaml`, `alpha_vantage.yaml`).

### Runner / orchestration

- **`tests/verify/runner.py`** — per-intent runner. Builds a config via `_build_config()` (mirrors `agent_assist/config.py`), invokes the orchestrator for the chosen intent with a seeded random ticker / vague prompt, captures the resulting `RunResult` (or exception), checks shape, returns a `PassResult` dataclass.
- **`tests/verify/matrix.py`** — top-level entry. Flags: `--reps N` (default 1), `--seed K`, `--only <intent>`, `--skip-backtest`. Iterates intents and tiers, prints a colored pass/fail board (rich), writes a JSONL log to `tests/verify/.runs/<timestamp>.jsonl` and a markdown summary to the same directory. Non-zero exit on any red.
- **`scripts/verify.ps1`** — Windows entry: `./scripts/verify.ps1` = single pass; `./scripts/verify.ps1 -Reps 3` = 3-pass green-streak loop.
- **`Makefile` targets** — `verify`, `verify-3x`, `verify-fast` (robustness + shape only, no directional / no contract). For convenience; `make` is optional on Windows.

### Determinism aids

- Top-level random seed is derived from the pass number alone, not wall-clock. Any non-deterministic source (network jitter, model sampling) is logged but does not break repeatability of *which test inputs we used*.
- Ratings are LLM outputs and remain non-deterministic by design — the pass criteria explicitly allow rating variation.

## Test taxonomy (Stage 2 deliverables)

All tests gated by markers so partial runs are easy.

### Robustness tier — `tests/verify/test_intent_<name>.py` (one per intent)

Files:
- `test_intent_specific.py`
- `test_intent_theme.py`
- `test_intent_budget.py`
- `test_intent_owned.py`
- `test_intent_compare.py`
- `test_intent_news_scan.py`
- `test_intent_freeform.py`

Each parameterises over `pass_no in [0, 1, 2]` and asserts:
- `exit_code == 0`
- `result.rating in {"Buy","Overweight","Hold","Underweight","Sell","SKIPPED"}` (FAILED is a fail)
- no uncaught exception
- `summary_path.exists()` and is non-empty

Marker: `@pytest.mark.verify_robustness`.

### Shape tier — `tests/verify/test_shape_<agent>.py`

One file per structured-output decision agent (Research Manager, Trader, Portfolio Manager). Asserts:
- Pydantic instance from `with_structured_output(Schema)` validates.
- Rendered markdown matches the per-agent header contract (`**Recommendation**:`, `**Action**:`, `**Rating**:`, `FINAL TRANSACTION PROPOSAL: **...**`).
- `render_*` round-trip identity: re-parsing the rendered markdown reconstructs an equivalent Pydantic instance.

Marker: `@pytest.mark.verify_shape`.

### Directional sanity tier — `tests/verify/test_backtest_directional.py`

For each row in `backtest_dataset.csv`:
- run the full pipeline with `today=row.analysis_date`,
- map the rating to a direction (Buy/Overweight → positive, Sell/Underweight → negative, Hold → neutral),
- assert direction matches `expected_direction`.

Pass condition for the suite: at least 4 of 5 rows match. The failing row(s), if any, are logged with the reasoning markdown so the user can inspect.

Marker: `@pytest.mark.verify_directional`.

### Contract tier — `tests/contract/test_client_<provider>.py`

One file per cloud LLM client + one for AlphaVantage. Uses `vcr.py` with `record_mode="none"` so any network egress fails the test. Asserts:
- The client builds the documented request shape (method, URL, headers, body schema).
- The response parser handles the recorded response without raising.
- Provider-specific structured-output binding (`json_schema` / `response_schema` / tool-use) is exercised.

Marker: `@pytest.mark.verify_contract`.

## Stage 3 — the 3x verification loop

This is the only stage that uses reactive multi-agent dispatch.

```
green_streak = 0
while green_streak < 3:
    result = run_matrix()            # writes JSONL + markdown summary
    if result.all_green:
        green_streak += 1
        log(f"pass clean; streak {green_streak}/3")
        continue

    # Red — fan out per failure
    for failure in result.failures:
        if recur_budget(failure.signature) > 3:
            pause_and_surface(failure)
            return

        debug    = spawn_subagent(
            type="general-purpose",
            skill="superpowers:systematic-debugging",
            scope=failure.file,
            input=failure.report,
        )
        coder    = spawn_subagent(
            type="general-purpose",
            skill="superpowers:test-driven-development",
            input=debug.root_cause + failure.report,
        )
        review   = spawn_subagent(
            subagent_type="code-review",
            input=coder.diff,
        )
        if review.approved:
            apply(coder.diff)
            commit(coder.diff, msg=f"fix(verify): {failure.signature}")
        else:
            requeue(failure, attempts=failure.attempts + 1)

    green_streak = 0
```

- The main session is the loop driver — it does NOT spawn agents reflexively, only on red and only one set of (debug → coder → review) per failing test.
- Each spawn matches the global subagent rule: file-domain isolated, >10 tool calls likely, no shared mutable state with the main loop because the diff is reviewed and applied in the main session.
- A failure with the same signature recurring three times pauses the loop and surfaces to the user.

## Error handling and escape hatches

| Failure mode | Detection | Response |
|---|---|---|
| Ollama unreachable on `localhost:11434` | `runner.py` health check before pass | Fail fast, point at `steps-to-production.md` Phase 2 |
| Analyst loops without calling tools | Per-phase elapsed > 5× expected | Mark test `skip("tool fluency drift on <model>")`, surface in summary |
| Malformed structured-output JSON | Pydantic validation error in shape tier | Escalation ladder from `steps-to-production.md` Phase 3 (swap quick → `qwen2.5-coder:7b`, then both → `qwen3-coder:30b`) |
| Backtest fixture stale (yfinance NaN at analysis date) | Pre-check during harness load | Regenerate the row at the nearest valid date, log the swap |
| Cassette drift (SDK bump changes wire shape) | Contract test diff against cassette | Fail with explicit "regenerate cassette" command in the failure message |
| yfinance rate limit / transient 5xx | Existing retry decorator | Already handled in `dataflows/`; surface in pass log if it eats >10% of budget |

## Definition of done

`./scripts/verify.ps1 -Reps 3` exits 0 three times in a row, on three consecutive invocations, with:
- every intent covered in robustness tier,
- every decision agent covered in shape tier,
- ≥4 of 5 directional fixtures matching,
- every cloud client + AlphaVantage cassette playing back cleanly,
- the green-streak log preserved at `tests/verify/.runs/`.

## Out of scope

- Replacing the LangGraph topology or adding new analysts.
- Cloud LLM live integration tests (covered by VCR cassettes only).
- Performance / latency tier (not requested; could be added later).
- The interactive `tradingagents analyze` CLI (Phase 4B in `steps-to-production.md` — different surface, can be a follow-up).
- Memory log resolution loop (touched indirectly by repeat runs; explicit test is a follow-up).
- Checkpoint resume on crash (manual smoke covered by Phase 5; explicit chaos test is a follow-up).
