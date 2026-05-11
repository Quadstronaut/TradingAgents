# Reddit sentiment cache contract

TradingAgents reads Reddit sentiment from a local on-disk cache populated
by a separate scraper project. This document is the contract the scraper
must follow; TradingAgents will never reach back to the scraper.

## Path

```
~/.tradingagents/reddit_sentiment/<TICKER>.jsonl
```

- One file per ticker
- Ticker portion of the filename is sanitised by
  `tradingagents.dataflows.utils.safe_ticker_component` on read; the
  scraper should write tickers in their canonical yfinance form
  (`BRK-B`, `RY-TO`, `NVDA`) for portability
- File is append-only JSONL. TradingAgents only reads; it does not rotate
  or trim. The scraper owns lifecycle and may rotate as it sees fit
- Override the directory by setting `TRADINGAGENTS_CACHE_DIR` (the
  `reddit_sentiment` subdirectory is appended automatically)

## Record format

One JSON object per line. UTF-8 encoded.

### Required fields

| Field        | Type   | Notes                                                   |
|--------------|--------|---------------------------------------------------------|
| `ts`         | string | ISO 8601 UTC timestamp, e.g. `"2026-05-11T13:55:00Z"`   |
| `sub`        | string | Subreddit name without the `r/` prefix                  |
| `score`      | number | Sentiment in `[-1.0, 1.0]`. Negative = bearish          |
| `confidence` | number | Scraper's confidence in `[0.0, 1.0]`                    |
| `n_posts`    | int    | How many posts/comments fed this aggregate              |

### Optional fields

| Field              | Type   | Notes                                              |
|--------------------|--------|----------------------------------------------------|
| `theme`            | string | One short phrase, e.g. `"AI demand still strong"`  |
| `learning_version` | string | Free-form version of the scraper's model           |
| `sample_quote`     | string | One representative quote (kept short)              |

### Example

```jsonl
{"ts":"2026-05-11T13:55:00Z","sub":"wallstreetbets","score":0.74,"confidence":0.81,"n_posts":42,"theme":"AI demand still strong","learning_version":"v3","sample_quote":"Blackwell ramp looking real"}
{"ts":"2026-05-11T14:10:00Z","sub":"investing","score":0.31,"confidence":0.63,"n_posts":18,"theme":"valuation concerns","learning_version":"v3"}
```

## TradingAgents reading behavior

- Reads the last 7 days by default (the Social Analyst can override
  `lookback_days`)
- Weights each record by `confidence × log(n_posts + 1)` when computing
  the aggregate score
- Surfaces up to three themes per subreddit and up to two sample quotes
- Missing file → returns "no cache available", analyst continues
- Malformed JSON line → logged and skipped, processing continues
- Record missing required fields → logged and skipped
- Records older than the lookback window are filtered out silently

## Versioning

This contract is v1. Additions are non-breaking. If the scraper needs to
emit a breaking change, bump a separate `schema_version` field at the top
of each record so this side can detect and ignore unknown shapes.
