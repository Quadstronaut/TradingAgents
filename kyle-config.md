# Run TradingAgents fully local with Ollama

No API keys, no cloud calls. Works on your existing setup.

## 1. Install Ollama + pull a model

Download from https://ollama.com/download/windows. Then in a new PowerShell:

```powershell
ollama pull qwen2.5-coder:7b      # ~4.7 GB, what you asked for
# or one of the catalog defaults:
ollama pull qwen3:latest          # 8B, recommended quick
ollama pull gpt-oss:latest        # 20B, stronger deep
ollama pull glm-4.7-flash:latest  # 30B, strongest local

ollama serve                       # leaves it listening on localhost:11434
```

Verify it's up: `curl http://localhost:11434/api/tags` should list your models.

## 2. Edit `main.py`

Replace the config block with:

```python
config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "ollama"
config["backend_url"] = "http://localhost:11434/v1"
config["deep_think_llm"]  = "qwen2.5-coder:7b"
config["quick_think_llm"] = "qwen2.5-coder:7b"
config["max_debate_rounds"] = 1
config["max_risk_discuss_rounds"] = 1
```

(The deep/quick split exists so you can run a heavier model for the Research Manager and Portfolio Manager — e.g. `gpt-oss:latest` for deep, `qwen2.5-coder:7b` for quick. Same model in both fields is fine.)

No `OPENAI_API_KEY` needed — the OpenAI client auto-injects `api_key="ollama"` when the provider is `ollama`.

## 3. Run

```powershell
uv run python main.py            # scripted NVDA example
uv run tradingagents analyze     # interactive — pick "Ollama" at the provider prompt
```

The CLI's Ollama menu only lists `qwen3 / gpt-oss / glm-4.7-flash` by default. To use `qwen2.5-coder:7b` from the CLI, pick any Ollama model on the menu, then override in code (or just stay with `python main.py`).

## Notes / gotchas

- **7B is small for this pipeline.** Expect weaker debate quality and occasional malformed structured-output JSON from Research Manager / Trader / Portfolio Manager. If you see schema errors, switch deep model to `gpt-oss:latest` or `glm-4.7-flash:latest`.
- **Tool calling**: `qwen2.5-coder` supports OpenAI-style tools, which is what the analysts need. If an analyst loops without ever calling tools, swap to `qwen3:latest` (better tool fluency).
- **Context window**: Ollama defaults to 4k. For long analyst prose set `OLLAMA_CONTEXT_LENGTH=16384` in env before `ollama serve`, or set per-model via `Modelfile` `PARAMETER num_ctx 16384`.
- **Data still hits the internet** — yfinance fetches prices/news. Only the LLM is local.
- **No API keys at all**: leave `.env` empty (or skip it). Tests still pass because `conftest.py` injects placeholders.

## Faster iteration

Cut tokens per run:

```python
ta = TradingAgentsGraph(
    selected_analysts=["market", "news"],   # drop social + fundamentals
    debug=True,
    config=config,
)
```
