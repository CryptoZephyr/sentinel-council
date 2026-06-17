# Sentinel Council

An autonomous multi-asset trading agent that synthesizes **five independent market perspectives** into a single weighted decision before simulating any trade. Built for the Bitget AI Base Camp Hackathon S1 — Track 1: Trading Agent.

> Five specialist analysts. One collective decision. Zero guesswork.

**Live dashboard → [sentinel-council01.streamlit.app](https://sentinel-council01.streamlit.app/)**

---

## What it does

Every hour, Sentinel Council runs a full intelligence cycle across **6 symbols** — BTCUSDT, ETHUSDT, SOLUSDT, BGBUSDT, AVAXUSDT, DOGEUSDT:

| Phase | What happens |
|---|---|
| **Perception** | Five signal functions query real Bitget futures data (ticker, candles, funding rate, open interest) and the Alternative.me Fear & Greed API, producing a score (0–100) per analyst per symbol |
| **Council** | A weighted confidence score combines all five: Macro 30% · Technical 30% · Sentiment 20% · News 10% · Intel 10% |
| **Decision** | ≥ 58 → BUY · ≤ 42 → SELL · otherwise → HOLD |
| **Risk** | Position size: 1% of balance (confidence 58–84) or 2% (≥ 85). Max 1 position per symbol, max 6 concurrent. Stop-loss −2%, take-profit +5% enforced each cycle |
| **Execution** | `SimPortfolio` opens/closes simulated positions using real Bitget prices. Balance starts at $10,000 USDT |
| **Audit** | Every decision appended to `trades.csv` — timestamp, symbol, decision, confidence, action, size, balance, PnL, full explanation |

---

## Five Signals

| Analyst | Weight | Data source | What it measures |
|---|---|---|---|
| **Macro** | 30% | Bitget MCP — `futures_get_ticker` (BTCUSDT) | BTC 24h momentum + funding rate as shared crypto regime signal |
| **Technical** | 30% | Bitget MCP — `futures_get_candles` (50 × 1h) | EMA9/EMA21 trend gap + RSI(14) contrarian score |
| **Sentiment** | 20% | Alternative.me Fear & Greed API + Bitget per-symbol funding rate | Contrarian F&G scoring (extreme fear = buy signal) + funding pressure |
| **News** | 10% | CoinTelegraph · CoinDesk · Decrypt · CryptoBriefing (RSS) | Per-symbol keyword filtering → bullish/bearish ratio across live headlines |
| **Intel** | 10% | Bitget MCP — `futures_get_ticker` + `futures_get_open_interest` | Per-symbol 24h price delta + OI change as institutional flow confirmation |

All scores are computed with pure numeric math — no external LLM calls, no keyword normalizer in the live path.

---

## Bitget modules used

- **Bitget MCP Server** (`npx -y bitget-mcp-server`) — `futures_get_ticker`, `futures_get_candles`, `futures_get_funding_rate`, `futures_get_open_interest`
- **Bitget REST API** — `GET /api/v2/mix/market/ticker` (real-time price feed for portfolio execution and dashboard)

---

## Dashboard

Mission Control dark-theme Streamlit dashboard — auto-refreshes every 10 seconds.

| Section | What it shows |
|---|---|
| **Live price strip** | All 6 pairs with real-time prices + 24h % change |
| **Portfolio status** | Balance, total PnL, closed trades, win rate |
| **Equity curve** | Portfolio balance over time vs $10,000 baseline |
| **Open positions monitor** | Live unrealized PnL per position with entry price, current price, SL/TP levels |
| **Signal intelligence matrix** | 6×5 color-coded heatmap — every symbol vs every analyst score at a glance |
| **Current signals** | Per-symbol confidence gauge, decision, dominant analyst, score bars |
| **Decision timeline** | Scatter plot of every BUY/SELL/HOLD decision per symbol over time |
| **Skill breakdown** | Per-analyst score bars + summary text, tabbed by symbol |
| **Confidence trend** | Multi-line chart with BUY/SELL threshold markers for all 6 symbols |
| **Decision log** | Full 25-row audit trail with CSV download |
| **Live news ticker** | TV-style scrolling bar with real-time crypto headlines (fixed bottom) |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  SENTINEL COUNCIL                   │
├─────────────────────────────────────────────────────┤
│  PERCEPTION LAYER         (Bitget MCP + REST)       │
│  ┌─────────┐ ┌──────────┐ ┌───────────┐            │
│  │  Macro  │ │Technical │ │ Sentiment │            │
│  │  30%    │ │   30%    │ │   20%     │            │
│  └────┬────┘ └────┬─────┘ └─────┬─────┘           │
│  ┌────┴────┐ ┌────┴─────┐       │                  │
│  │  News   │ │  Intel   │       │                  │
│  │  10%    │ │   10%    │       │                  │
│  └────┬────┘ └────┬─────┘       │                  │
│       └───────────┴─────────────┘                  │
│                   │                                 │
│  COUNCIL ENGINE ──┤ weighted confidence 0–100       │
│                   │ BUY ≥58 · SELL ≤42 · HOLD       │
│                   │                                 │
│  RISK ENGINE ─────┤ 1–2% of balance per position   │
│                   │ SL −2% · TP +5%                │
│                   │ max 6 concurrent positions      │
│                   │                                 │
│  EXECUTION ───────┤ SimPortfolio ($10,000 USDT)     │
│                   │ real Bitget prices              │
│                   │                                 │
│  AUDIT TRAIL ─────┤ trades.csv · sentinel.log       │
└─────────────────────────────────────────────────────┘
```

---

## Install

```bash
pip install -r requirements.txt
```

Requires Node.js for the Bitget MCP server:

```bash
npx -y bitget-mcp-server   # downloads automatically on first run
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
# Run one full cycle across all 6 symbols, then exit
python sentinel.py --once

# Continuous hourly loop (production mode)
python sentinel.py

# Validate keyword normalizer (test mode)
python sentinel.py --test
```

---

## Run the dashboard

```bash
# Standard
streamlit run dashboard.py

# Microsoft Store Python (Windows)
python3.12 -m streamlit run dashboard.py
```

---

## File structure

```
sentinel-council/
├── sentinel.py          # Agent: all five phases + hourly loop
├── dashboard.py         # Streamlit Mission Control dashboard
├── requirements.txt     # Python dependencies
├── trades.csv           # Audit trail (committed as evidence)
├── .env                 # Credentials (never committed)
├── logs/
│   └── sentinel.log     # Runtime log
└── data/
    ├── portfolio.json   # Simulated portfolio state (persisted)
    └── cycle_status.json  # Live cycle state for dashboard countdown
```

---

## Submission

- **Hackathon**: Bitget AI Base Camp S1 — Track 1: Trading Agent
- **Deadline**: June 25, 2026
- **Builder**: CryptoZephyr
- **Dashboard**: https://sentinel-council01.streamlit.app/
- **Repo**: https://github.com/CryptoZephyr/sentinel-council
