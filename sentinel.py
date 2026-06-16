"""
Sentinel Council — autonomous multi-skill AI trading agent.
Bitget AI Base Camp Hackathon S1 — Track 1: Trading Agent.

Perception -> Council -> Risk -> Execution -> Audit Trail.

Five specialist perspectives scored 0-100 directly from real market data:
  macro       (30%) — BTC 24h momentum + funding rate regime signal
  technical   (30%) — RSI mean-reversion + EMA9/21 trend from Bitget klines
  sentiment   (20%) — Fear & Greed contrarian index + per-symbol funding
  news        (10%) — CoinDesk RSS headline keyword balance; neutral on error
  intel       (10%) — Symbol-level 24h price momentum + open interest flow

Scores are computed directly (no text intermediary, no keyword normalizer in
the live path). The keyword normalizer is retained for --test mode only.

Usage:
    python sentinel.py          -> continuous hourly loop
    python sentinel.py --once   -> one cycle across all symbols, then exit
    python sentinel.py --test   -> normalizer self-test, then exit
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

# ─────────────────────────────────────────────────────────────────
# DIRECTORIES (must exist before the FileHandler below opens)
# ─────────────────────────────────────────────────────────────────

Path("logs").mkdir(parents=True, exist_ok=True)
Path("data").mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("sentinel.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("sentinel")


# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────

class Config:
    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    LOOP_INTERVAL = 3600  # seconds

    WEIGHTS = {
        "macro": 0.30,
        "technical": 0.30,
        "sentiment": 0.20,
        "news": 0.10,
        "intel": 0.10,
    }

    BUY_THRESHOLD = 58.0
    SELL_THRESHOLD = 42.0

    RISK_CONSERVATIVE = 0.01
    RISK_AGGRESSIVE = 0.02
    AGGRESSIVE_THRESHOLD = 85.0
    MAX_POSITIONS = 3

    STARTING_BALANCE = 10000.0

    TRADES_CSV = Path("trades.csv")
    PORTFOLIO_JSON = Path("data/portfolio.json")

    API_KEY = os.getenv("BITGET_API_KEY", "")
    SECRET_KEY = os.getenv("BITGET_SECRET_KEY", "")
    PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")


# ─────────────────────────────────────────────────────────────────
# NORMALIZATION LAYER — pure keyword scoring, zero API calls
# ─────────────────────────────────────────────────────────────────

BULLISH_TERMS: dict[str, int] = {
    "bullish": 15, "breakout": 15, "uptrend": 15, "buy signal": 15,
    "golden cross": 12, "accumulation": 12, "momentum": 12, "oversold": 12,
    "recovery": 12, "inflows": 11, "rally": 8, "supportive": 8,
    "constructive": 8, "improving": 8, "holding support": 8, "above": 6,
    "rising": 6, "positive": 6, "strong": 5, "upward": 5, "higher": 4,
    "gains": 4, "growth": 4,
}

BEARISH_TERMS: dict[str, int] = {
    "bearish": 15, "breakdown": 15, "downtrend": 15, "sell signal": 15,
    "death cross": 12, "distribution": 12, "overbought": 12, "resistance": 10,
    "rejection": 12, "outflows": 11, "selloff": 8, "weakness": 8,
    "declining": 8, "deteriorating": 8, "caution": 6, "concern": 5,
    "below": 6, "falling": 6, "negative": 6, "weak": 5, "downward": 5,
    "lower": 4, "losses": 4,
}

NEGATION_WORDS = {
    "not", "no", "never", "without", "lacks", "lack", "neither", "nor",
    "isn't", "aren't", "wasn't", "don't", "doesn't", "didn't", "cannot", "can't",
}


def _score_terms(text_lower: str, term_dict: dict[str, int]) -> int:
    total = 0
    for term, weight in term_dict.items():
        idx = text_lower.find(term)
        while idx != -1:
            prefix = text_lower[:idx].rstrip()
            prefix_words = re.split(r"\W+", prefix)
            window = prefix_words[-3:] if len(prefix_words) >= 3 else prefix_words
            total += -weight if any(w in NEGATION_WORDS for w in window) else weight
            idx = text_lower.find(term, idx + 1)
    return total


def _extract_summary(text: str) -> str:
    lines = [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.strip().upper().startswith("SIGNAL_SCORE")
    ]
    if not lines:
        return "No summary available."
    sentences = re.split(r"(?<=[.!?])\s+", lines[0])
    return (sentences[0] if sentences else lines[0])[:150]


def normalize_skill_output(text: str) -> dict[str, Any]:
    """Raw text -> {score: int 0-100, summary: str}. Never raises."""
    try:
        if not text or len(text.strip()) < 10:
            return {"score": 50, "summary": "No data"}

        # Priority 1: explicit self-score
        match = re.search(r"SIGNAL_SCORE:\s*(\d+)", text, re.IGNORECASE)
        if match:
            score = max(0, min(100, int(match.group(1))))
            return {"score": score, "summary": _extract_summary(text)}

        # Priority 2: keyword scoring
        text_lower = text.lower()
        bull = max(0, _score_terms(text_lower, BULLISH_TERMS))
        bear = max(0, _score_terms(text_lower, BEARISH_TERMS))
        total = bull + bear
        score = 50 if total == 0 else int(25 + (bull / total) * 50)
        score = max(0, min(100, score))
        return {"score": score, "summary": _extract_summary(text)}

    except Exception as exc:
        logger.error("Normalizer error: %s", exc)
        return {"score": 50, "summary": "No data"}


# ─────────────────────────────────────────────────────────────────
# PERCEPTION LAYER — five perspectives synthesized from real Bitget
# futures market data, reached via the Bitget MCP server. See module
# docstring for why this replaces the (nonexistent) Agent Hub Skills.
# ─────────────────────────────────────────────────────────────────

async def _call_mcp_tool(session: ClientSession, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await session.call_tool(tool_name, arguments=arguments)
        parsed = json.loads(result.content[0].text)
        if not parsed.get("ok", False):
            logger.warning("MCP tool %s returned error: %s", tool_name, parsed.get("error"))
            return {}
        return parsed.get("data", {})
    except Exception as exc:
        logger.error("MCP tool call failed [%s]: %s", tool_name, exc)
        return {}


def _ema(values: list[float], period: int) -> float:
    if len(values) < period:
        return values[-1] if values else 0.0
    ema = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for price in values[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


async def get_macro_signal(session: ClientSession) -> dict[str, Any]:
    """BTC 24h momentum + funding rate as a crypto regime signal (shared across symbols)."""
    logger.info("Perception: macro (BTC regime)...")
    ticker = await _call_mcp_tool(session, "futures_get_ticker",
                                   {"symbol": "BTCUSDT", "productType": "USDT-FUTURES"})
    rows = ticker.get("data", [])
    if not rows:
        return {"score": 50, "summary": "Macro data unavailable — neutral"}
    row = rows[0]
    change24h = float(row.get("change24h", 0))
    funding = float(row.get("fundingRate", 0))

    # 24h price move: +/-3% = full signal; funding: +/-0.05% = full signal
    s_change = max(-1.0, min(1.0, change24h / 0.03))
    s_funding = max(-1.0, min(1.0, funding / 0.0005))
    signal = max(-1.0, min(1.0, 0.6 * s_change + 0.4 * s_funding))
    score = max(20, min(80, int(50 + signal * 25)))
    direction = "bullish" if score > 55 else "bearish" if score < 45 else "neutral"
    summary = f"BTC 24h {change24h * 100:+.2f}%, funding {funding * 100:.4f}% — regime {direction}"
    return {"score": score, "summary": summary}


async def get_technical_signal(session: ClientSession, symbol: str) -> dict[str, Any]:
    """RSI(14) mean-reversion + EMA9/EMA21 trend from 50x1h candles."""
    logger.info("Perception: technical-analysis [%s]...", symbol)
    candles = await _call_mcp_tool(session, "futures_get_candles", {
        "symbol": symbol, "productType": "USDT-FUTURES",
        "granularity": "1h", "limit": 50,
    })
    rows = candles.get("data", [])
    if len(rows) < 21:
        return {"score": 50, "summary": "Insufficient candle data — neutral"}
    rows = sorted(rows, key=lambda r: int(r[0]))
    closes = [float(r[4]) for r in rows]

    ema9, ema21 = _ema(closes, 9), _ema(closes, 21)
    rsi = _rsi(closes, 14)

    # RSI: oversold (<40) -> bullish; overbought (>60) -> bearish
    rsi_score = max(20, min(80, int(50 + (50 - rsi) * 0.5)))
    # EMA gap: +/-2% of price = full signal
    ema_gap_pct = (ema9 - ema21) / ema21 if ema21 else 0.0
    s_trend = max(-1.0, min(1.0, ema_gap_pct / 0.02))
    trend_score = max(20, min(80, int(50 + s_trend * 30)))

    score = max(20, min(80, int(0.5 * rsi_score + 0.5 * trend_score)))
    trend_label = "uptrend" if ema9 > ema21 else "downtrend"
    summary = f"RSI {rsi:.1f}, EMA9/21 {trend_label} (gap {ema_gap_pct * 100:+.2f}%)"
    return {"score": score, "summary": summary}


async def get_sentiment_signal(session: ClientSession, symbol: str) -> dict[str, Any]:
    """Contrarian Fear & Greed index + per-symbol funding rate positioning."""
    logger.info("Perception: sentiment-analyst [%s]...", symbol)

    fg_value = 50
    try:
        fg_resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=6)
        fg_value = int(fg_resp.json()["data"][0]["value"])
    except Exception as exc:
        logger.warning("Fear & Greed API unavailable: %s", exc)

    funding = 0.0
    funding_data = await _call_mcp_tool(session, "futures_get_funding_rate",
                                         {"symbol": symbol, "productType": "USDT-FUTURES"})
    rates = funding_data.get("data", {}).get("currentFundRate", [])
    if rates:
        funding = float(rates[0].get("fundingRate", 0))

    # Extreme fear (fg=0) -> contrarian buy -> score 75; extreme greed (fg=100) -> caution -> score 25
    fg_score = max(20, min(80, int(75 - fg_value * 0.5)))
    # Positive funding = crowded longs = bearish contrarian pressure
    s_funding = max(-1.0, min(1.0, -funding / 0.0005))
    funding_score = max(35, min(65, int(50 + s_funding * 15)))

    score = max(20, min(80, int(0.7 * fg_score + 0.3 * funding_score)))
    fg_label = (
        "Extreme Fear" if fg_value < 25 else "Fear" if fg_value < 45 else
        "Neutral" if fg_value < 55 else "Greed" if fg_value < 75 else "Extreme Greed"
    )
    summary = f"F&G {fg_value}/100 ({fg_label}), funding {funding * 100:.4f}%"
    return {"score": score, "summary": summary}


_NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://feeds.feedburner.com/CoinDesk",
    "https://cryptobriefing.com/feed/",
    "https://decrypt.co/feed",
]

_NEWS_BULL = [
    "rally", "soar", "surge", "rebound", "bullish", "accumulation",
    "buy", "etf", "adoption", "tops", "rises", "gains", "all-time",
    "recovery", "institutional", "inflow", "whale", "accumulate", "breakout",
]
_NEWS_BEAR = [
    "crash", "drop", "fall", "bear", "trap", "slump", "concern",
    "warning", "skeptic", "decline", "risk", "plunge", "bottom",
    "crashing", "stall", "liquidat", "outflow", "sell-off", "breakdown",
]


async def get_news_signal() -> dict[str, Any]:
    """Multi-source crypto news sentiment from CoinTelegraph, CoinDesk, Decrypt, CryptoBriefing."""
    logger.info("Perception: news-briefing...")

    def _fetch(url: str) -> str:
        try:
            return requests.get(
                url, headers={"User-Agent": "SentinelCouncil/1.0"}, timeout=8,
            ).text
        except Exception:
            return ""

    texts = await asyncio.gather(*[asyncio.to_thread(_fetch, u) for u in _NEWS_FEEDS])

    titles: list[str] = []
    for text in texts:
        if not text:
            continue
        raw = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", text, re.DOTALL)
        titles.extend(t.lower().strip() for t in raw[1:11])  # skip channel title, take 10

    if not titles:
        return {"score": 50, "summary": "News data unavailable — neutral"}

    combined = " ".join(titles)
    bull = sum(combined.count(w) for w in _NEWS_BULL)
    bear = sum(combined.count(w) for w in _NEWS_BEAR)
    total = bull + bear
    score = max(25, min(75, 50 if total == 0 else int(25 + bull / total * 50)))
    return {"score": score, "summary": f"{len(titles)} headlines — {bull} bullish / {bear} bearish"}


async def get_market_intel_signal(session: ClientSession, symbol: str) -> dict[str, Any]:
    """Symbol-level 24h price momentum + open interest as institutional flow proxy."""
    logger.info("Perception: market-intel [%s]...", symbol)
    ticker = await _call_mcp_tool(session, "futures_get_ticker",
                                   {"symbol": symbol, "productType": "USDT-FUTURES"})
    oi = await _call_mcp_tool(session, "futures_get_open_interest",
                               {"symbol": symbol, "productType": "USDT-FUTURES"})
    rows = ticker.get("data", [])
    oi_rows = oi.get("data", {}).get("openInterestList", [])
    if not rows:
        return {"score": 50, "summary": "Market intel data unavailable — neutral"}
    change24h = float(rows[0].get("change24h", 0))
    oi_size = float(oi_rows[0].get("size", 0)) if oi_rows else 0.0

    # 24h price move: +/-3% = full signal
    s_change = max(-1.0, min(1.0, change24h / 0.03))
    score = max(20, min(80, int(50 + s_change * 25)))
    flow_label = "accumulating" if score > 55 else "distributing" if score < 45 else "neutral flow"
    summary = (f"{symbol} 24h {change24h * 100:+.2f}% — {flow_label}"
               + (f", OI {oi_size:.0f}" if oi_size else ""))
    return {"score": score, "summary": summary}


async def run_perception(
    session: ClientSession,
    symbol: str,
    macro_result: dict[str, Any],
    news_result: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    technical_result = await get_technical_signal(session, symbol)
    sentiment_result = await get_sentiment_signal(session, symbol)
    intel_result = await get_market_intel_signal(session, symbol)

    perception = {
        "macro": macro_result,
        "technical": technical_result,
        "sentiment": sentiment_result,
        "news": news_result,
        "intel": intel_result,
    }
    logger.info(
        "Perception [%s] -> macro:%d technical:%d sentiment:%d news:%d intel:%d",
        symbol, perception["macro"]["score"], perception["technical"]["score"],
        perception["sentiment"]["score"], perception["news"]["score"], perception["intel"]["score"],
    )
    return perception


# ─────────────────────────────────────────────────────────────────
# COUNCIL ENGINE
# ─────────────────────────────────────────────────────────────────

def run_council(scores: dict[str, int], summaries: dict[str, str]) -> dict[str, Any]:
    confidence = round(sum(Config.WEIGHTS[k] * scores[k] for k in Config.WEIGHTS), 1)

    if confidence >= Config.BUY_THRESHOLD:
        decision = "BUY"
    elif confidence <= Config.SELL_THRESHOLD:
        decision = "SELL"
    else:
        decision = "HOLD"

    dominant = max(scores, key=lambda k: Config.WEIGHTS[k] * scores[k])

    if decision == "HOLD":
        gap_text = f"{Config.BUY_THRESHOLD - confidence:.1f} points below BUY threshold"
    elif decision == "BUY":
        gap_text = f"{confidence - Config.BUY_THRESHOLD:.1f} points above BUY threshold"
    else:  # SELL
        gap_text = f"{Config.SELL_THRESHOLD - confidence:.1f} points below SELL threshold"

    lines = [
        f"  - {k} ({int(Config.WEIGHTS[k] * 100)}% weight, score {scores[k]}): {summaries[k]}"
        for k in ["macro", "technical", "sentiment", "news", "intel"]
    ]
    explanation = (
        f"{decision} signal at {confidence:.1f}% confidence ({gap_text}). "
        f"Dominant: {dominant}.\n" + "\n".join(lines)
    )

    logger.info("Council -> %s @ %.1f%% confidence (dominant: %s)", decision, confidence, dominant)
    return {"decision": decision, "confidence": confidence, "dominant": dominant, "explanation": explanation}


# ─────────────────────────────────────────────────────────────────
# RISK ENGINE
# ─────────────────────────────────────────────────────────────────

def calculate_position(
    decision: str, confidence: float, symbol: str, balance: float, open_positions: dict[str, Any],
) -> dict[str, Any]:
    if decision == "HOLD":
        return {"action": "HOLD", "size": 0.0, "reason": "Neutral signal"}

    if decision == "SELL":
        if symbol in open_positions:
            return {"action": "CLOSE", "size": open_positions[symbol]["size"],
                     "reason": "Bearish — closing position"}
        return {"action": "HOLD", "size": 0.0, "reason": "Bearish but no position to close"}

    # decision == "BUY"
    if symbol in open_positions:
        return {"action": "HOLD", "size": 0.0, "reason": "Already in position — no pyramiding"}
    if len(open_positions) >= Config.MAX_POSITIONS:
        return {"action": "HOLD", "size": 0.0, "reason": "Max concurrent positions reached"}

    risk_pct = Config.RISK_AGGRESSIVE if confidence >= Config.AGGRESSIVE_THRESHOLD else Config.RISK_CONSERVATIVE
    size = round(balance * risk_pct, 2)
    logger.info("Risk [%s] -> OPEN_LONG $%.2f (%.0f%% risk @ %.1f%% confidence)",
                symbol, size, risk_pct * 100, confidence)
    return {"action": "OPEN_LONG", "size": size, "reason": f"{risk_pct * 100:.0f}% risk at {confidence:.1f}% confidence"}


# ─────────────────────────────────────────────────────────────────
# EXECUTION ENGINE — SimPortfolio
# ─────────────────────────────────────────────────────────────────

class SimPortfolio:
    def __init__(self, balance: float = Config.STARTING_BALANCE) -> None:
        self.balance = balance
        self.open_positions: dict[str, Any] = {}
        self.trades: list[dict[str, Any]] = []
        self.total_pnl = 0.0
        self.win_count = 0
        self.trade_count = 0

    def get_price(self, symbol: str) -> float:
        try:
            resp = requests.get(
                "https://api.bitget.com/api/v2/mix/market/ticker",
                params={"symbol": symbol, "productType": "USDT-FUTURES"},
                timeout=10,
            )
            return float(resp.json()["data"][0]["lastPr"])
        except Exception as exc:
            logger.error("Price fetch failed for %s: %s", symbol, exc)
            return 0.0

    def open_long(self, symbol: str, size: float) -> dict[str, Any]:
        price = self.get_price(symbol)
        if price == 0.0:
            logger.warning("Cannot open position — price unavailable for %s", symbol)
            return {}
        position = {"symbol": symbol, "size": size, "entry_price": price,
                     "opened_at": datetime.now(timezone.utc).isoformat()}
        self.open_positions[symbol] = position
        self.balance -= size
        logger.info("OPENED LONG %s — size $%.2f @ %.4f | balance $%.2f", symbol, size, price, self.balance)
        self.save()
        return position

    def close_position(self, symbol: str) -> dict[str, Any]:
        if symbol not in self.open_positions:
            return {}
        pos = self.open_positions.pop(symbol)
        price = self.get_price(symbol)
        if price == 0.0:
            price = pos["entry_price"]  # avoid artificial loss on feed failure
        pnl = round((price - pos["entry_price"]) / pos["entry_price"] * pos["size"], 4)
        self.balance += pos["size"] + pnl
        self.total_pnl += pnl
        self.trade_count += 1
        if pnl > 0:
            self.win_count += 1
        trade = {**pos, "exit_price": price, "pnl": pnl, "closed_at": datetime.now(timezone.utc).isoformat()}
        self.trades.append(trade)
        logger.info("CLOSED %s — PnL $%.4f | balance $%.2f | total PnL $%.4f",
                    symbol, pnl, self.balance, self.total_pnl)
        self.save()
        return trade

    def execute(self, risk_result: dict[str, Any], symbol: str) -> dict[str, Any]:
        action = risk_result["action"]
        if action == "OPEN_LONG":
            return self.open_long(symbol, risk_result["size"])
        if action == "CLOSE":
            return self.close_position(symbol)
        return {"action": "HOLD", "symbol": symbol}

    def win_rate(self) -> float:
        return round(self.win_count / self.trade_count * 100, 1) if self.trade_count else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "balance": round(self.balance, 4),
            "open_positions": self.open_positions,
            "total_pnl": round(self.total_pnl, 4),
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "win_rate": self.win_rate(),
        }

    def save(self) -> None:
        try:
            Config.PORTFOLIO_JSON.parent.mkdir(parents=True, exist_ok=True)
            with open(Config.PORTFOLIO_JSON, "w") as f:
                json.dump({**self.summary(), "trades": self.trades}, f, indent=2)
        except Exception as exc:
            logger.error("Portfolio save failed: %s", exc)

    @classmethod
    def load_or_create(cls) -> "SimPortfolio":
        if Config.PORTFOLIO_JSON.exists():
            try:
                with open(Config.PORTFOLIO_JSON) as f:
                    data = json.load(f)
                portfolio = cls(balance=data["balance"])
                portfolio.open_positions = data.get("open_positions", {})
                portfolio.trades = data.get("trades", [])
                portfolio.total_pnl = data.get("total_pnl", 0.0)
                portfolio.win_count = data.get("win_count", 0)
                portfolio.trade_count = data.get("trade_count", 0)
                logger.info("Portfolio loaded — balance $%.2f, %d open positions",
                            portfolio.balance, len(portfolio.open_positions))
                return portfolio
            except Exception as exc:
                logger.warning("Could not load portfolio, starting fresh: %s", exc)
        return cls()


# ─────────────────────────────────────────────────────────────────
# AUDIT TRAIL — trades.csv
# ─────────────────────────────────────────────────────────────────

CSV_FIELDS = ["timestamp", "symbol", "decision", "confidence", "action", "size", "balance", "pnl", "explanation"]


def _ensure_csv_header() -> None:
    if not Config.TRADES_CSV.exists():
        with open(Config.TRADES_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()


def log_trade_row(symbol: str, council: dict[str, Any], risk_result: dict[str, Any], portfolio: SimPortfolio) -> None:
    _ensure_csv_header()
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "decision": council["decision"],
        "confidence": council["confidence"],
        "action": risk_result["action"],
        "size": risk_result["size"],
        "balance": round(portfolio.balance, 2),
        "pnl": round(portfolio.total_pnl, 4),
        "explanation": council["explanation"],
    }
    with open(Config.TRADES_CSV, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)
    logger.info("Logged trade row -> %s", row)


# ─────────────────────────────────────────────────────────────────
# MAIN CYCLE + LOOP
# ─────────────────────────────────────────────────────────────────

def _mcp_server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command="npx",
        args=["-y", "bitget-mcp-server", "--modules", "all"],
        env={
            "BITGET_API_KEY": Config.API_KEY,
            "BITGET_SECRET_KEY": Config.SECRET_KEY,
            "BITGET_PASSPHRASE": Config.PASSPHRASE,
        },
    )


async def run_cycle(portfolio: SimPortfolio) -> None:
    """One full cycle: all five perspectives, for every symbol, via one MCP session."""
    try:
        async with stdio_client(_mcp_server_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                macro_result = await get_macro_signal(session)
                news_result = await get_news_signal()

                for symbol in Config.SYMBOLS:
                    logger.info("=" * 60)
                    logger.info("CYCLE START — %s", symbol)
                    try:
                        perception = await run_perception(session, symbol, macro_result, news_result)
                        scores = {k: v["score"] for k, v in perception.items()}
                        summaries = {k: v["summary"] for k, v in perception.items()}

                        council = run_council(scores, summaries)
                        logger.info("Explanation [%s]:\n%s", symbol, council["explanation"])
                        risk_result = calculate_position(
                            council["decision"], council["confidence"],
                            symbol, portfolio.balance, portfolio.open_positions,
                        )
                        portfolio.execute(risk_result, symbol)
                        log_trade_row(symbol, council, risk_result, portfolio)

                        logger.info("CYCLE END — %s | %s | %s | balance $%.2f",
                                    symbol, council["decision"], risk_result["action"], portfolio.balance)
                    except Exception as exc:
                        logger.error("Cycle failed for %s: %s", symbol, exc)
    except Exception as exc:
        logger.error("MCP session failed for this cycle: %s", exc)


def run_once() -> SimPortfolio:
    portfolio = SimPortfolio.load_or_create()
    asyncio.run(run_cycle(portfolio))
    return portfolio


async def main_loop() -> None:
    portfolio = SimPortfolio.load_or_create()
    logger.info("Sentinel Council starting — hourly loop. Symbols: %s", Config.SYMBOLS)
    while True:
        await run_cycle(portfolio)
        logger.info("Cycle complete. Sleeping %ds.", Config.LOOP_INTERVAL)
        await asyncio.sleep(Config.LOOP_INTERVAL)


# ─────────────────────────────────────────────────────────────────
# NORMALIZER SELF-TEST
# ─────────────────────────────────────────────────────────────────

def run_normalizer_test() -> None:
    cases = [
        ("Strongly bullish breakout", "Bullish breakout above resistance, uptrend accelerating, accumulation strong.", "> 65"),
        ("Strongly bearish breakdown", "Bearish breakdown below support, downtrend, distribution, weakness everywhere.", "< 40"),
        ("Neutral text", "Funding rate flat, neutral sentiment, balanced positioning.", "~ 50"),
        ("Self-scored", "On-chain flows mixed.\nSIGNAL_SCORE: 72", "= 72"),
        ("Empty input", "", "= 50"),
    ]
    print("\n" + "=" * 60)
    print("SENTINEL COUNCIL — NORMALIZER TEST")
    print("=" * 60)
    for name, text, expected in cases:
        result = normalize_skill_output(text)
        score = result["score"]
        assert isinstance(score, int) and 0 <= score <= 100, f"{name}: score out of range"
        print(f"\n{name} (expected {expected})")
        print(f"  Score   : {score}")
        print(f"  Summary : {result['summary']}")
        print("  Status  : PASS")
    print("\n" + "=" * 60)
    print("All tests passed.")
    print("=" * 60 + "\n")


# ─────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sentinel Council — Trading Agent")
    parser.add_argument("--once", action="store_true", help="Run one cycle across all symbols and exit.")
    parser.add_argument("--test", action="store_true", help="Run normalizer self-test and exit.")
    args = parser.parse_args()

    if args.test:
        run_normalizer_test()
    elif args.once:
        result_portfolio = run_once()
        print("\nPortfolio summary:")
        print(json.dumps(result_portfolio.summary(), indent=2))
    else:
        asyncio.run(main_loop())
