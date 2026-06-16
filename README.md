# Sentinel Council

Sentinel Council is an autonomous trading agent built for the Bitget AI Base Camp Hackathon S1 (Track 1: Trading Agent). It synthesizes five independent market perspectives — macro, technical, sentiment, news, and market-intel — into a single weighted decision before simulating any trade. It runs against real Bitget price and market data but trades against a simulated $10,000 USDT portfolio, never real capital.

## Bitget modules used

- Bitget MCP Server (`bitget-mcp-server`) — `futures_get_ticker`, `futures_get_candles`, `futures_get_funding_rate`, `futures_get_open_interest`
- Bitget REST API — `GET /api/v2/mix/market/ticker` (real-time price feed for the simulated portfolio)

**Note on scope:** the five Bitget "Agent Hub Skills" named in the original spec (`macro-analyst`, `technical-analysis`, `sentiment-analyst`, `news-briefing`, `market-intel`) do not exist as callable tools on the Bitget MCP server or REST API (confirmed by listing the server's actual tools and testing the documented endpoint). The five perspectives are instead computed from the real market data above. `news-briefing` has no real data source on this API surface and is logged as unavailable every cycle rather than faked. Full detail is in `00_TASK.txt`'s Issues and Decisions logs.

## Install

```
pip install -r requirements.txt
```

## Configure

Create a `.env` file in the project root with:

```
BITGET_API_KEY=your_key
BITGET_SECRET_KEY=your_secret
BITGET_PASSPHRASE=your_passphrase
```

## Run

```
python sentinel.py --test   # validate the normalizer, then exit
python sentinel.py --once   # run one full cycle across all symbols, then exit
python sentinel.py          # continuous hourly loop
```

## Run the dashboard

```
streamlit run dashboard.py
```

## Architecture

```
Perception -> Decision -> Risk Management -> Execution -> Audit Trail
```

- **Perception** — real Bitget market data (ticker, candles, funding rate, open interest) is fetched per symbol and turned into descriptive text for each of the five perspectives.
- **Decision** — a keyword-based normalizer scores each perspective 0–100; the Council Engine combines them into a single weighted confidence score and a BUY/SELL/HOLD decision.
- **Risk Management** — the Risk Engine sizes positions by confidence band, with hard limits on concurrent positions and no pyramiding.
- **Execution** — `SimPortfolio` opens/closes simulated positions against real Bitget prices and tracks balance, PnL, and win rate.
- **Audit Trail** — every decision is appended to `trades.csv` (timestamp, symbol, decision, confidence, action, size, balance, pnl, explanation), and every step is logged to `sentinel.log`.
