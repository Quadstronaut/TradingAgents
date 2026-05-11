"""Provider/model constants for the agent-assist orchestrator.

Mirrors the local-Ollama setup configured in main.py. Keeping these as
module constants lets tests import + monkeypatch them.
"""

from typing import Final


# Stage 1 (shortlist) — quick model, structured-output JSON.
SHORTLIST_PROVIDER: Final = "ollama"
SHORTLIST_MODEL: Final = "qwen3:8b"
SHORTLIST_BASE_URL: Final = "http://localhost:11434/v1"

# Stage 2 (deep analysis) — full TradingAgents pipeline. These mirror main.py.
ANALYSIS_PROVIDER: Final = "ollama"
ANALYSIS_DEEP_MODEL: Final = "qwen3-coder:30b"
ANALYSIS_QUICK_MODEL: Final = "qwen3:8b"
ANALYSIS_BASE_URL: Final = "http://localhost:11434/v1"
