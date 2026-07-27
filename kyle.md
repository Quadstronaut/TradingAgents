# Quadstronaut's quickstart (Windows + uv)

You're already set up. Only thing missing: an API key.

## 1. Create `.env`

```powershell
copy .env.example .env
notepad .env
```

Fill in **at least one** key (whichever provider you'll use). For `main.py` as-is, it's OpenAI:

```
OPENAI_API_KEY=sk-...
```

## 2. Run

```powershell
uv run python main.py            # scripted NVDA example
uv run tradingagents analyze     # interactive CLI
```

That's it. Venv stays active across reboots — just `cd` here and run `uv run ...`.

## Switch provider (optional)

Edit `main.py`:

```python
config["llm_provider"] = "anthropic"          # or google, xai, deepseek, qwen, glm
config["deep_think_llm"] = "claude-sonnet-4-6"
config["quick_think_llm"] = "claude-haiku-4-5"
```

Then put the matching key in `.env` (`ANTHROPIC_API_KEY=...`, etc).

## Notes

- Ignore Gemini's conda advice — `uv` is what you used and it's fine.
- `uv run` auto-activates the venv; you don't need `Activate.ps1`.
- Outputs land in `~/.tradingagents/` (logs, memory, checkpoints).
- First run will hit yfinance for prices — no extra key needed.
