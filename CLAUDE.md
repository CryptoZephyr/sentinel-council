# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Sentinel Council is an autonomous multi-skill AI trading agent for the Bitget AI Base Camp Hackathon S1 (Track 1: Trading Agent). It synthesizes five independent market perspectives before executing any trade.

**Core philosophy**: Five specialist analysts are better than one signal source. Uses real Bitget price feeds but simulated portfolio (no real capital).

## Quick Commands

```bash
# Run the agent (hourly loop)
python sentinel.py

# Run one analysis cycle across all symbols
python sentinel.py --once

# Validate the normalizer (keyword scoring)
python sentinel.py --test

# Run the dashboard (separate process)
streamlit run dashboard.py
```

## Critical Architecture — Five Phases

The system is organized into **five sequential phases** that must all be present and traceable:

1. **Perception Layer**: Five analytical functions query underlying Bitget MCP tools and RSS/API data directly. The named Agent Hub Skills are not exposed as callable tools by `bitget-mcp-server`; this implementation maps the same analyst roles onto real available tools.

2. **Normalization Layer**: Retained for `python sentinel.py --test` only. The live path computes numeric scores directly from market data, with no external LLM API calls.

3. **Council Engine**: Combines five scores using fixed weights (macro 30%, technical 30%, sentiment 20%, news 10%, intel 10%) into one confidence score. Decision rules: `confidence >= 72 → BUY`, `<= 28 → SELL`, `60–71` or `29–40 → WATCH`, otherwise `WAIT`. `HOLD` is not a valid backend state.

4. **Risk Engine**: Translates BUY/SELL/WATCH/WAIT into executable simulated actions. Position sizing: 1% of balance for BUY scores 72–74, 2% for 75+. WATCH/WAIT do not open new positions. Hard limits: max 1 position per symbol, max 6 concurrent positions, SL −2% / TP +5%, no pyramiding, and no same-cycle re-entry after an automatic SL/TP close.

5. **Execution Engine**: `SimPortfolio` simulates trades using real Bitget REST prices with per-symbol sanity bounds. Tracks open positions, closed trades, PnL, win rate, and persists state to `data/portfolio.json` after each trade.

## Build Rules — Non-Negotiable Constraints

Read `04_BUILD_RULES.txt` for the complete list. Key constraints:

- **Exactly three code files**: `sentinel.py`, `dashboard.py`, `requirements.txt`
- **All five analyst roles must run** every cycle (or log a failure, never silently skip)
- **Zero external LLM API calls** — normalization is keyword-based only
- **No extra agents, frameworks, or ML** — this is a single-process, pure-logic agent
- **Real prices only** — use Bitget REST API for market prices
- **Simulated portfolio only** — no real trading, starting balance $10,000 USDT
- **Type hints on all functions** and every external call wrapped in try/except

## Data Files & Directories

- `logs/sentinel.log` — runtime log (created at startup)
- `trades.csv` — **audit trail** (one row per decision or automatic SL/TP close, critical for submission)
- `data/cycle_status.json` — dashboard cycle state
- `data/portfolio.json` — persisted sim portfolio state (balance, open positions, trade history)
- `.env` — Bitget credentials (API_KEY, SECRET_KEY, PASSPHRASE) — never commit

## Skill Calling & MCP Integration

The code uses MCP stdio with a pinned package:

```python
StdioServerParameters(
    command="npx",
    args=["-y", "bitget-mcp-server@1.1.0", "--modules", "all"],
)
```

The live tools are `futures_get_ticker`, `futures_get_candles`, `futures_get_funding_rate`, and `futures_get_open_interest`. RSS feeds cover the news role because no Bitget MCP news tool is available.

## Test-Only Keyword Scoring Details

The `--test` normalizer scores sample text by counting keyword occurrences with negation awareness. This is not used in the live trading path:

- **Bullish terms** (weighted): bullish, buy, uptrend, breakout, accumulation, oversold, recovery, rate-cut, ETF inflows, whale accumulation, golden cross, etc.
- **Bearish terms** (weighted): bearish, sell, downtrend, breakdown, distribution, overbought, resistance, rate-hike, ETF outflows, whale selling, death cross, etc.
- **Negation words** (3-word window): not, no, never, lacks, isn't, can't, etc. — these flip the score weight of nearby terms.

Formula: `score = 25 + (bullish_count / (bullish_count + bearish_count)) * 50`, clamped 0–100.

**Special case**: If Skill output contains `SIGNAL_SCORE: N`, parse and use `N` directly (already normalized by the Skill).

## Config Class Parameters

These are hardcoded in `Config` and should match `04_BUILD_RULES.txt`:

```python
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BGBUSDT", "AVAXUSDT", "DOGEUSDT"]
LOOP_INTERVAL = 3600  # seconds (1 hour)
BUY_THRESHOLD = 72.0
SELL_THRESHOLD = 28.0
WATCH_BUY_THRESHOLD = 60.0
WATCH_SELL_THRESHOLD = 40.0
RISK_PCT_CONSERVATIVE = 0.01   # 1% for confidence 72–74
RISK_PCT_AGGRESSIVE = 0.02     # 2% for confidence 75+
AGGRESSIVE_THRESHOLD = 75.0
MAX_POSITIONS = 6
SCORE_MIN = 5
SCORE_MAX = 95
SL_PCT = -0.02   # stop-loss -2%
TP_PCT = 0.05    # take-profit +5%
STARTING_BALANCE = 10000.0
```

Changing these requires updating `04_BUILD_RULES.txt` and re-confirming all status markers.

## Logging & Errors

- **Logger setup** at module level using both `StreamHandler` (console) and `FileHandler` (logs/sentinel.log)
- **Every external call** (MCP, RSS, price API) is wrapped in try/except — single-symbol failure must never crash the loop
- **Log at INFO level** for each phase: perception → council → risk → execute
- **Log at ERROR level** for API failures, then return safe defaults (`50` for unavailable signals, `0.0` for unavailable prices)

## Audit Trail & Evidence

**Critical for hackathon submission**:

- `trades.csv` must have 20+ rows spanning multiple hours before submission (proof of autonomous operation)
- Each row captures: timestamp, symbol, confidence, decision, action, size, price, quantity, balance change, rank, action distance, portfolio balance, cumulative PnL, per-trade PnL, and explanation text containing all five analyst scores
- Automatic SL/TP exits are logged as `CLOSE` rows so dashboard win rate and CSV history use the same source of truth

The CSV is committed to GitHub and judges must be able to verify results.

## Testing the Normalizer

Run `python sentinel.py --test` to validate keyword scoring against 6 test cases:
- Bullish macro (Fed easing, DXY softening, ETF inflows)
- Bearish technical (death cross, distribution, RSI low)
- Neutral sentiment (Fear & Greed at 51, balanced funding)
- Self-scored news (contains SIGNAL_SCORE: 72)
- Bullish market intel (whale accumulation, ETF inflows)
- Empty input (defaults to neutral 50)

All tests must pass before committing; test results printed with scores and directions.

## Submission Checklist

Before final submission, confirm all rules in `04_BUILD_RULES.txt`:

- [ ] Section A (what you must build) — all 8 rules CONFIRMED
- [ ] Section B (what you must not build) — all 10 rules CONFIRMED (none added)
- [ ] Section C (code quality) — all 6 rules CONFIRMED
- [ ] Section D (configuration) — all 6 values match hardcoded Config
- [ ] Section E (submission rules) — all 6 rules CONFIRMED

## Known Implementation Notes

- **Sequential symbol processing**: Symbols are processed one after another (not concurrently) to avoid MCP server contention
- **Price feed fallback**: If price fetch fails during position close, uses entry price (avoids artificial losses on network errors)
- **Win rate calculation**: Only counts closed trades (unrealised PnL not included)
- **Portfolio persistence**: `SimPortfolio` auto-saves after every trade — state survives restarts
