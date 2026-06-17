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

1. **Perception Layer** (lines 340–471): Calls all five Bitget Agent Hub Skills concurrently (macro-analyst, technical-analysis, sentiment-analyst, news-briefing, market-intel) for each symbol.

2. **Normalization Layer** (lines 128–293): Converts raw Skill text → `{ score: 0-100, summary: str }` using pure keyword scoring. **Zero external LLM API calls**. Priority: (1) look for `SIGNAL_SCORE: N` in response, (2) count bullish/bearish keywords with negation-aware weighting, (3) default to 50 (neutral).

3. **Council Engine** (lines 474–546): Combines normalized scores using fixed weights (macro 30%, technical 30%, sentiment 20%, news 10%, intel 10%) into a single weighted confidence (0–100). Decision rules: `confidence >= 75 → BUY`, `<= 35 → SELL`, otherwise `HOLD`.

4. **Risk Engine**: Translates BUY/SELL/HOLD into sized actions. Position sizing: 1% of balance for confidence 58–84, 2% for 85+. Hard limits: max 1 position per symbol, max 6 concurrent positions, SL −2% / TP +5%, no pyramiding.

5. **Execution Engine** (lines 610–741): `SimPortfolio` class simulates trades using real prices from Bitget REST API. Tracks open positions, closed trades, PnL, win rate. Persists state to `data/portfolio.json` after each trade.

## Build Rules — Non-Negotiable Constraints

Read `04_BUILD_RULES.txt` for the complete list. Key constraints:

- **Exactly three code files**: `sentinel.py`, `dashboard.py`, `requirements.txt`
- **All five Skills must be called** every cycle (or log a failure, never silently skip)
- **Zero external LLM API calls** — normalization is keyword-based only
- **No extra agents, frameworks, or ML** — this is a single-process, pure-logic agent
- **Real prices only** — use Bitget REST API for market prices
- **Simulated portfolio only** — no real trading, starting balance $10,000 USDT
- **Type hints on all functions** and every external call wrapped in try/except

## Data Files & Directories

- `logs/sentinel.log` — runtime log (created at startup)
- `logs/decisions.csv` — **audit trail** (one row per decision, critical for submission)
- `logs/cycles/*.json` — full cycle record per symbol, includes raw Skill outputs (proof)
- `data/portfolio.json` — persisted sim portfolio state (balance, open positions, trade history)
- `.env` — Bitget credentials (API_KEY, SECRET_KEY, PASSPHRASE) — never commit

## Skill Calling & MCP Integration

The original code calls Bitget Skills via HTTP REST API (not MCP):

```python
def _call_skill(skill_name: str, params: dict) -> str:
    url = f"{Config.BITGET_BASE_URL}/agent-hub/v1/skills/{skill_name}"
    # POST with HMAC-SHA256 signature via _bitget_headers()
```

**If migrating to MCP** (via `npx -y bitget-mcp-server`): update `_call_skill()` to use `StdioServerParameters` + `ClientSession.call_tool()` as documented in `01_INSTRUCTIONS_FOR_CLAUDE.txt` (lines 36–59). All five Skills must be called per symbol per cycle.

## Keyword Scoring Details

The normalizer scores Skill text by counting keyword occurrences with negation awareness:

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
BUY_THRESHOLD = 58.0
SELL_THRESHOLD = 42.0
RISK_PCT_CONSERVATIVE = 0.01   # 1% for confidence 58–84
RISK_PCT_AGGRESSIVE = 0.02     # 2% for confidence 85+
AGGRESSIVE_THRESHOLD = 85.0
MAX_POSITIONS = 6
SL_PCT = -0.02   # stop-loss -2%
TP_PCT = 0.05    # take-profit +5%
STARTING_BALANCE = 10000.0
```

Changing these requires updating `04_BUILD_RULES.txt` and re-confirming all status markers.

## Logging & Errors

- **Logger setup** at module level using both `StreamHandler` (console) and `FileHandler` (logs/sentinel.log)
- **Every external call** (Skill, price API) is wrapped in try/except — single-symbol failure must never crash the loop
- **Log at INFO level** for each phase: perception → council → risk → execute
- **Log at ERROR level** for API failures, then return safe default (empty string for Skills, 0.0 for prices)

## Audit Trail & Evidence

**Critical for hackathon submission**:

- `logs/decisions.csv` must have 20+ rows spanning multiple hours before submission (proof of autonomous operation)
- Each row captures: timestamp, symbol, all five skill scores, confidence, decision, action, portfolio balance, total PnL, win rate
- `logs/cycles/*.json` stores full cycle data including raw Skill outputs (auditable proof)

The CSV is committed to GitHub and judges must be able to verify results.

## Testing the Normalizer

Run `python sentinel.py --test` to validate keyword scoring against 6 test cases:
- Bullish macro (Fed easing, DXY softening, ETF inflows)
- Bearish technical (death cross, distribution, RSI low)
- Neutral sentiment (Fear & Greed at 51, balanced funding)
- Self-scored news (contains SIGNAL_SCORE: 58)
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
