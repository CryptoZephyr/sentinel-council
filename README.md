# Sentinel Council

An autonomous multi-asset trading agent that synthesizes **five independent market perspectives** into a single weighted decision before simulating any trade. Built for the Bitget AI Base Camp Hackathon S1 — Track 1: Trading Agent.

> Five specialist analysts. One collective decision. Zero guesswork.

---

## What it does

Every hour, Sentinel Council runs a full intelligence cycle across **BTCUSDT**, **ETHUSDT**, and **SOLUSDT**:

| Phase | What happens |
|---|---|
| **Perception** | Five signal functions query real Bitget futures data (ticker, candles, funding rate, open interest) and the Alternative.me Fear & Greed API, producing a score (0–100) per analyst per symbol |
| **Council** | A weighted confidence score combines all five: Macro 30% · Technical 30% · Sentiment 20% · News 10% · Intel 10% |
| **Decision** | ≥ 58 → BUY · ≤ 42 → SELL · otherwise → HOLD |
| **Risk** | Position size: 1% of balance (confidence 58–84) or 2% (≥ 85). Hard limits: max 1 position per symbol, max 3 concurrent |
| **Execution** | `SimPortfolio` opens/closes simulated positions using real Bitget prices. Balance starts at $10,000 USDT |
| **Audit** | Every decision appended to `trades.csv` — timestamp, symbol, decision, confidence, action, size, balance, PnL, full explanation |

---

## Five Signals

| Analyst | Data source | What it measures |
|---|---|---|
| **Macro** | Bitget MCP — `futures_get_ticker` (BTCUSDT) | BTC 24h momentum + funding rate as crypto regime signal |
| **Technical** | Bitget MCP — `futures_get_candles` (50 × 1h) | EMA9/EMA21 trend gap + RSI(14) contrarian score |
| **Sentiment** | Alternative.me Fear & Greed API + Bitget funding rate | Contrarian F&G scoring (fear = buy signal) + funding pressure |
| **News** | CoinTelegraph · CoinDesk · Decrypt · CryptoBriefing (RSS) | Bullish/bearish keyword ratio across 40 live headlines |
| **Intel** | Bitget MCP — `futures_get_ticker` + `futures_get_open_interest` | Per-symbol 24h price change + open interest as institutional flow proxy |

All scores are computed with pure numeric math — no external LLM calls, no keyword normalizer in the live path.

---

## Bitget modules used

- **Bitget MCP Server** (`npx -y bitget-mcp-server`) — `futures_get_ticker`, `futures_get_candles`, `futures_get_funding_rate`, `futures_get_open_interest`
- **Bitget REST API** — `GET /api/v2/mix/market/ticker` (real-time price feed for portfolio execution)

---

## Dashboard

A Mission Control dark-theme Streamlit dashboard with:

- **Live price strip** — BTC / ETH / SOL prices + 24h % change from Bitget REST API (60s cache)
- **Confidence arc gauges** — SVG semi-circle speedometer per symbol, color-coded BUY/SELL/HOLD
- **Equity curve** — Portfolio balance over time vs $10,000 baseline
- **Five-skill score bars** — Color-coded per signal direction (green ≥ 60, amber 40–59, red < 40)
- **Decision timeline** — Scatter plot of every BUY/SELL/HOLD decision per symbol over time
- **Confidence trend** — Line chart with BUY/SELL threshold markers
- **Live news ticker** — TV-style scrolling bar with real-time crypto headlines
- **Decision log** — Full audit trail with download

---

## Install

```bash
pip install -r requirements.txt
```

Requires Node.js for the Bitget MCP server:

```bash
npx -y bitget-mcp-server   # downloads on first run
```

---

## Configure

Create `.env` in the project root:

```
BITGET_API_KEY=your_key
BITGET_SECRET_KEY=your_secret
BITGET_PASSPHRASE=your_passphrase
```

---

## Run

```bash
# Validate normalizer (6 test cases)
python sentinel.py --test

# One full cycle across all three symbols, then exit
python sentinel.py --once

# Continuous hourly loop (production mode)
python sentinel.py
```

---

## Run the dashboard

```bash
# If streamlit is on PATH
streamlit run dashboard.py

# Microsoft Store Python (common on Windows)
python3.12 -m streamlit run dashboard.py
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  SENTINEL COUNCIL                   │
├─────────────────────────────────────────────────────┤
│  PERCEPTION LAYER                                   │
│  ┌─────────┐ ┌──────────┐ ┌───────────┐            │
│  │  Macro  │ │Technical │ │ Sentiment │            │
│  │  30%    │ │   30%    │ │   20%     │            │
│  └────┬────┘ └────┬─────┘ └─────┬─────┘           │
│       │           │             │                  │
│  ┌────┴────┐ ┌────┴─────┐       │                  │
│  │  News   │ │  Intel   │       │                  │
│  │  10%    │ │   10%    │       │                  │
│  └────┬────┘ └────┬─────┘       │                  │
│       └───────────┴─────────────┘                  │
│                   │                                 │
│  COUNCIL ENGINE ──┤ weighted confidence 0–100       │
│                   │ BUY ≥58 · SELL ≤42 · HOLD       │
│                   │                                 │
│  RISK ENGINE ─────┤ size 1–2% of balance            │
│                   │ max 3 concurrent positions       │
│                   │                                 │
│  EXECUTION ───────┤ SimPortfolio ($10,000 USDT)     │
│                   │ real Bitget prices              │
│                   │                                 │
│  AUDIT TRAIL ─────┤ trades.csv · sentinel.log       │
└─────────────────────────────────────────────────────┘
```

---

## File structure

```
sentinel-council/
├── sentinel.py       # Agent: all five phases
├── dashboard.py      # Streamlit dashboard
├── requirements.txt  # Python dependencies
├── trades.csv        # Audit trail (committed as evidence)
├── .env              # Credentials (never committed)
├── logs/
│   └── sentinel.log  # Runtime log (not committed)
└── data/
    └── portfolio.json  # Simulated portfolio state
```

---

## Submission

- **Hackathon**: Bitget AI Base Camp S1 — Track 1: Trading Agent
- **Deadline**: June 25, 2026
- **Builder**: Tommy (CryptoZephyr)
- **Dashboard**: https://sentinel-council-8w83xzappwvtzkhaymveqqt.streamlit.app/
- **Repo**: https://github.com/CryptoZephyr/sentinel-council
