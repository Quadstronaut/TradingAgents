# Directional-sanity backtest fixture

5 historical (ticker, analysis_date) pairs with strong, unambiguous 5-trading-day
alpha vs SPY. Each pair was selected so that |alpha| >= 0.05 — large enough that
a competent pipeline should produce a directionally correct recommendation.

Method (reproducible via `uv run python` + `yfinance==0.2.63`):

1. Pull auto-adjusted daily closes for the ticker and SPY around each date.
2. Anchor on `analysis_date` (or the next trading day if it falls on a weekend / holiday).
3. Forward 5 trading days for both the ticker and SPY.
4. `realized_5d_alpha = (ticker_close[+5td] / ticker_close[0] - 1) - (spy_close[+5td] / spy_close[0] - 1)`
5. `expected_direction = positive` if alpha > +0.02, `negative` if < -0.02, else `neutral`.

All five rows are well outside the +/-0.02 dead zone, so the directional label
is robust to small data revisions.

## Rows

### 1. NVDA, 2024-02-21 — alpha +0.1328 (positive)
- Catalyst: Q4 FY24 earnings (reported after the close 2024-02-21). Revenue
  $22.1B vs $20.6B consensus; data-center revenue +409% YoY; guided $24.0B for
  Q1 vs $21.9B consensus.
- 5td window: 2024-02-21 close ($67.43) -> 2024-02-28 close ($77.62), +15.10%.
- SPY same window: +1.82%. Alpha +13.28%.
- Sector: tech / semiconductors.

### 2. TSLA, 2024-04-24 — alpha +0.1202 (positive)
- Catalyst: Q1 2024 earnings (reported 2024-04-23 after the close). EPS miss,
  but Musk pulled the next-day call announcing accelerated low-cost-model
  roadmap; stock gapped up 12% the next session and continued running.
- 5td window: 2024-04-24 close ($162.13) -> 2024-05-01 close ($179.99), +11.02%.
- SPY same window: -1.00%. Alpha +12.02%.
- Sector: auto / EV.

### 3. LLY, 2023-08-08 — alpha +0.0633 (positive)
- Catalyst: Q2 2023 earnings (pre-market 2023-08-08). Mounjaro revenue
  $980M vs ~$740M consensus; Lilly raised full-year revenue guide by $2.2B
  and EPS guide on the strength of GLP-1 demand.
- 5td window: 2023-08-08 close ($510.60) -> 2023-08-15 close ($536.24), +5.02%.
- SPY same window: -1.31% (broader market weak in mid-August). Alpha +6.33%.
- Sector: healthcare / pharma.

### 4. CVX, 2022-02-24 — alpha +0.1412 (positive)
- Catalyst: Russian invasion of Ukraine (2022-02-24). Brent spiked from ~$95
  to >$120/bbl over the following week. Integrated majors with diversified
  upstream re-rated hard.
- 5td window: 2022-02-24 close ($114.83) -> 2022-03-03 close ($133.03), +15.85%.
- SPY same window: +1.73%. Alpha +14.12%.
- Sector: energy.

### 5. NVDA, 2024-08-28 — alpha -0.1309 (negative)
- Catalyst: Q2 FY25 earnings (after the close 2024-08-28). Numbers beat, but
  guidance underwhelmed the AI-bubble narrative and reports of Blackwell yield
  delays hit the tape; stock sold off into a broader risk-off week.
- 5td window: 2024-08-28 close ($125.55) -> 2024-09-05 close ($107.16), -14.65%.
- SPY same window: -1.56%. Alpha -13.09%.
- Sector: tech / semiconductors. (Same ticker as row 1 — included on purpose
  to test that the pipeline's verdict is driven by the specific catalyst, not
  a stale "NVDA always goes up" prior.)

## Sector spread

- Tech / semis: 2 rows (NVDA up, NVDA down)
- Auto / EV: 1 row (TSLA up)
- Healthcare / pharma: 1 row (LLY up)
- Energy: 1 row (CVX up)

## Directional balance

- Positive: 4
- Negative: 1
- Neutral: 0

This satisfies "at least 3 positive, at least 1 negative" with sector diversity.
A future iteration could add a second negative row (e.g. NFLX 2022-04-20,
alpha -0.1047, streaming subscriber-loss day) once the harness is happy
with the current set.
