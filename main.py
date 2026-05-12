from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

# LOCAL CONFIG (Ollama, this machine). Upstream main.py uses OpenAI; on pull
# conflicts: `git stash push main.py` → `git pull` → `git stash pop`, resolve.
config = DEFAULT_CONFIG.copy()
config["llm_provider"]            = "ollama"
config["backend_url"]             = "http://localhost:11434/v1"
config["deep_think_llm"]          = "qwen3-coder:30b"
config["quick_think_llm"]         = "qwen3:8b"
config["max_debate_rounds"]       = 1
config["max_risk_discuss_rounds"] = 1

# Configure data vendors (default uses yfinance, no extra API keys needed)
config["data_vendors"] = {
    "core_stock_apis": "yfinance",           # Options: alpha_vantage, yfinance
    "technical_indicators": "yfinance",      # Options: alpha_vantage, yfinance
    "fundamental_data": "yfinance",          # Options: alpha_vantage, yfinance
    "news_data": "yfinance",                 # Options: alpha_vantage, yfinance
}

# Initialize with custom config
ta = TradingAgentsGraph(debug=False, config=config)

# forward propagate
_, decision = ta.propagate("NVDA", "2024-05-10")
print(decision)

# Memorize mistakes and reflect
# ta.reflect_and_remember(1000) # parameter is the position returns
