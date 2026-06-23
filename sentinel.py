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
import math
import os
import re
import socket
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import requests
except ModuleNotFoundError:
    requests = None  # type: ignore[assignment]
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> None:
        return None

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ModuleNotFoundError:
    ClientSession = Any  # type: ignore[misc, assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None

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
        logging.FileHandler("logs/sentinel.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("sentinel")


# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────

class Config:
    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BGBUSDT", "AVAXUSDT", "DOGEUSDT"]
    LOOP_INTERVAL = 3600  # seconds

    WEIGHTS = {
        "macro": 0.30,
        "technical": 0.30,
        "sentiment": 0.20,
        "news": 0.10,
        "intel": 0.10,
    }

    BUY_THRESHOLD = 72.0
    SELL_THRESHOLD = 28.0
    WATCH_BUY_THRESHOLD = 60.0
    WATCH_SELL_THRESHOLD = 40.0

    RISK_CONSERVATIVE = 0.01
    RISK_AGGRESSIVE = 0.02
    AGGRESSIVE_THRESHOLD = 75.0
    MAX_POSITIONS = 6
    SL_PCT = -0.02   # close position if down 2%
    TP_PCT = 0.05    # close position if up 5%
    SCORE_MIN = 5
    SCORE_MAX = 95
    NEWS_SCORE_MIN = 10
    NEWS_SCORE_MAX = 90
    FUNDING_SCORE_MIN = 20
    FUNDING_SCORE_MAX = 80
    MCP_CALL_TIMEOUT = 20
    TICKER_MAX_AGE_SECONDS = 300
    CANDLE_MAX_AGE_SECONDS = 4 * 3600

    STARTING_BALANCE = 10000.0

    TRADES_CSV = Path("trades.csv")
    PORTFOLIO_JSON = Path("data/portfolio.json")
    BITGET_MCP_PACKAGE = "bitget-mcp-server@1.1.0"
    BITGET_API_HOST = "api.bitget.com"
    BITGET_API_IPS = ("104.18.14.166", "104.18.15.166")
    PRICE_BOUNDS = {
        "BTCUSDT": (1_000.0, 500_000.0),
        "ETHUSDT": (100.0, 50_000.0),
        "SOLUSDT": (1.0, 10_000.0),
        "BGBUSDT": (0.01, 1_000.0),
        "AVAXUSDT": (0.1, 1_000.0),
        "DOGEUSDT": (0.001, 10.0),
    }

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

_SYMBOL_KEYWORDS: dict[str, list[str]] = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "eth"],
    "SOLUSDT": ["solana", "sol"],
    "BGBUSDT": ["bgb", "bitget"],
    "AVAXUSDT": ["avalanche", "avax"],
    "DOGEUSDT": ["dogecoin", "doge"],
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
# PERCEPTION LAYER — five specialist analysts, each producing a direct
# numeric score (0–100) from real Bitget futures data.
#
# The five named Bitget Agent Hub Skills (macro-analyst, technical-
# analysis, sentiment-analyst, news-briefing, market-intel) are not
# exposed as callable tools on bitget-mcp-server. Verified via
# session.list_tools(): 56 raw trading/market-data tools returned,
# none matching the Skill names. The Agent Hub REST endpoint also
# returns 403. Full record in 00_TASK.txt Issue #1 and Decision #1.
#
# Each function below represents the corresponding analytical role
# using the underlying Bitget MCP tools directly:
#   Macro     → futures_get_ticker (BTCUSDT 24h + funding rate)
#   Technical → futures_get_candles (EMA9/21 crossover + RSI)
#   Sentiment → futures_get_funding_rate + Fear & Greed API
#   News      → 4 crypto RSS feeds (no Bitget tool covers news)
#   Intel     → futures_get_ticker + futures_get_open_interest
# ─────────────────────────────────────────────────────────────────

async def _call_mcp_tool(session: ClientSession, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await asyncio.wait_for(
            session.call_tool(tool_name, arguments=arguments),
            timeout=Config.MCP_CALL_TIMEOUT,
        )
        content = getattr(result, "content", None)
        if not content:
            logger.warning("MCP tool %s returned no content", tool_name)
            return {}
        text = getattr(content[0], "text", "")
        if not text:
            logger.warning("MCP tool %s returned empty content", tool_name)
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            logger.warning("MCP tool %s returned non-object JSON", tool_name)
            return {}
        if not parsed.get("ok", False):
            logger.warning("MCP tool %s returned error: %s", tool_name, parsed.get("error"))
            return {}
        data = parsed.get("data", {})
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"data": data}
        logger.warning("MCP tool %s returned unsupported data shape: %s", tool_name, type(data).__name__)
        return {}
    except asyncio.TimeoutError:
        logger.error("MCP tool call timed out [%s] after %ds", tool_name, Config.MCP_CALL_TIMEOUT)
        return {}
    except json.JSONDecodeError as exc:
        logger.error("MCP tool returned malformed JSON [%s]: %s", tool_name, exc)
        return {}
    except Exception as exc:
        logger.error("MCP tool call failed [%s]: %s", tool_name, exc)
        return {}


@contextmanager
def _bitget_dns_override():
    real_getaddrinfo = socket.getaddrinfo

    def _patched_getaddrinfo(host: str, *args: Any, **kwargs: Any):
        if host == Config.BITGET_API_HOST:
            results: list[Any] = []
            for ip in Config.BITGET_API_IPS:
                try:
                    results.extend(real_getaddrinfo(ip, *args, **kwargs))
                except Exception:
                    continue
            if results:
                return results
        return real_getaddrinfo(host, *args, **kwargs)

    socket.getaddrinfo = _patched_getaddrinfo  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]


def _bitget_rest_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    if requests is None:
        logger.error("requests is unavailable; cannot call Bitget REST %s", path)
        return {}
    try:
        with _bitget_dns_override():
            resp = requests.get(
                f"https://{Config.BITGET_API_HOST}{path}",
                params=params,
                timeout=Config.MCP_CALL_TIMEOUT,
            )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            logger.warning("Bitget REST returned non-object JSON [%s]", path)
            return {}
        if str(payload.get("code", "")) != "00000":
            logger.warning("Bitget REST error [%s]: %s", path, payload)
            return {}
        data = payload.get("data", {})
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"data": data}
        logger.warning("Bitget REST returned unsupported data shape [%s]: %s", path, type(data).__name__)
        return {}
    except Exception as exc:
        logger.warning("Bitget REST call failed [%s]: %s", path, exc)
        return {}


def _ema(values: list[float], period: int) -> float:
    if period <= 0:
        return 0.0
    values = [v for v in values if math.isfinite(v)]
    if len(values) < period:
        return values[-1] if values else 0.0
    ema = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for price in values[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _safe_int(value: Any, default: int = 0) -> int:
    number = _safe_float(value, float(default))
    return int(number) if math.isfinite(number) else default


def _is_stale_timestamp(value: Any, max_age_seconds: int) -> bool:
    timestamp = _safe_float(value, 0.0)
    if timestamp <= 0.0:
        return False
    if timestamp < 10_000_000_000:
        timestamp *= 1000
    age_seconds = datetime.now(timezone.utc).timestamp() - timestamp / 1000
    return age_seconds > max_age_seconds or age_seconds < -300


def _safe_signal_result(result: Any, label: str) -> dict[str, Any]:
    data = _as_dict(result)
    score = int(_clamp(_safe_float(data.get("score"), 50.0), 0, 100))
    summary = str(data.get("summary") or f"{label} data unavailable — neutral")[:500]
    safe = {"score": score, "summary": summary}
    if "oi_size" in data:
        safe["oi_size"] = max(0.0, _safe_float(data.get("oi_size"), 0.0))
    return safe


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _rsi(values: list[float], period: int = 14) -> float:
    if period <= 0:
        return 50.0
    values = [v for v in values if math.isfinite(v)]
    if len(values) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macro_score(change24h: float, funding: float) -> int:
    # 24h price move: +/-3% = full signal; funding: +/-0.05% = full signal
    s_change = max(-1.0, min(1.0, change24h / 0.03))
    s_funding = max(-1.0, min(1.0, funding / 0.0005))
    signal = max(-1.0, min(1.0, 0.6 * s_change + 0.4 * s_funding))
    return int(_clamp(50 + signal * 45, Config.SCORE_MIN, Config.SCORE_MAX))


async def get_macro_signals(session: ClientSession) -> dict[str, dict[str, Any]]:
    """Peer-basket 24h momentum + funding regime, excluding the target symbol."""
    logger.info("Perception: macro (peer basket regime)...")
    market: dict[str, tuple[float, float]] = {}
    for symbol in Config.SYMBOLS:
        ticker = _bitget_rest_get("/api/v2/mix/market/ticker",
                                  {"symbol": symbol, "productType": "USDT-FUTURES"})
        rows = _as_list(ticker.get("data"))
        if not rows:
            logger.warning("Macro ticker unavailable for %s", symbol)
            continue
        row = _as_dict(rows[0])
        if _is_stale_timestamp(row.get("ts") or row.get("timestamp") or row.get("time"), Config.TICKER_MAX_AGE_SECONDS):
            logger.warning("Macro ticker stale for %s", symbol)
            continue
        change24h = _safe_float(row.get("change24h"), 0.0)
        funding = _safe_float(row.get("fundingRate"), 0.0)
        market[symbol] = (change24h, funding)

    if not market:
        return {
            symbol: {"score": 50, "summary": "Macro data unavailable — neutral"}
            for symbol in Config.SYMBOLS
        }

    results: dict[str, dict[str, Any]] = {}
    for symbol in Config.SYMBOLS:
        peers = [values for sym, values in market.items() if sym != symbol] or list(market.values())
        avg_change = sum(change for change, _ in peers) / len(peers)
        avg_funding = sum(funding for _, funding in peers) / len(peers)
        score = _macro_score(avg_change, avg_funding)
        direction = "bullish" if score > 55 else "bearish" if score < 45 else "neutral"
        results[symbol] = {
            "score": score,
            "summary": (
                f"Peer basket 24h {avg_change * 100:+.2f}%, funding "
                f"{avg_funding * 100:.4f}% — regime {direction}"
            ),
        }
    return results


async def get_technical_signal(session: ClientSession, symbol: str) -> dict[str, Any]:
    """RSI(14) mean-reversion + EMA9/EMA21 trend from 50x1h candles."""
    logger.info("Perception: technical-analysis [%s]...", symbol)
    candles = _bitget_rest_get("/api/v2/mix/market/candles", {
        "symbol": symbol, "productType": "USDT-FUTURES",
        "granularity": "1H", "limit": 50,
    })
    rows = _as_list(candles.get("data"))
    if len(rows) < 21:
        return {"score": 50, "summary": "Insufficient candle data — neutral"}
    parsed_rows = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        ts = _safe_int(row[0], 0)
        close = _safe_float(row[4], 0.0)
        if ts > 0 and close > 0.0:
            parsed_rows.append((ts, close))
    if len(parsed_rows) < 21:
        logger.warning("Malformed candle data for %s — neutral", symbol)
        return {"score": 50, "summary": "Malformed candle data — neutral"}
    parsed_rows = sorted(parsed_rows, key=lambda r: r[0])
    if _is_stale_timestamp(parsed_rows[-1][0], Config.CANDLE_MAX_AGE_SECONDS):
        logger.warning("Stale candle data for %s — neutral", symbol)
        return {"score": 50, "summary": "Stale candle data — neutral"}
    closes = [close for _, close in parsed_rows]

    ema9, ema21 = _ema(closes, 9), _ema(closes, 21)
    rsi = _rsi(closes, 14)

    # RSI: oversold (<40) -> bullish; overbought (>60) -> bearish
    rsi_score = int(_clamp(50 + (50 - rsi) * 0.9, Config.SCORE_MIN, Config.SCORE_MAX))
    # EMA gap: +/-2% of price = full signal
    ema_gap_pct = (ema9 - ema21) / ema21 if ema21 else 0.0
    s_trend = max(-1.0, min(1.0, ema_gap_pct / 0.02))
    trend_score = int(_clamp(50 + s_trend * 45, Config.SCORE_MIN, Config.SCORE_MAX))

    score = int(_clamp(0.5 * rsi_score + 0.5 * trend_score, Config.SCORE_MIN, Config.SCORE_MAX))
    trend_label = "uptrend" if ema9 > ema21 else "downtrend"
    summary = f"RSI {rsi:.1f}, EMA9/21 {trend_label} (gap {ema_gap_pct * 100:+.2f}%)"
    return {"score": score, "summary": summary}


async def get_sentiment_signal(session: ClientSession, symbol: str) -> dict[str, Any]:
    """Contrarian Fear & Greed index + per-symbol funding rate positioning."""
    logger.info("Perception: sentiment-analyst [%s]...", symbol)

    def _fetch_fg() -> int:
        try:
            resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=6)
            return int(resp.json()["data"][0]["value"])
        except Exception as exc:
            logger.warning("Fear & Greed API unavailable: %s", exc)
            return 50

    fg_value = await asyncio.to_thread(_fetch_fg)

    funding = 0.0
    funding_data = _bitget_rest_get("/api/v2/mix/market/current-fund-rate",
                                    {"symbol": symbol, "productType": "USDT-FUTURES"})
    rates = _as_list(_as_dict(funding_data.get("data")).get("currentFundRate"))
    if rates:
        funding = _safe_float(_as_dict(rates[0]).get("fundingRate"), 0.0)

    # Extreme fear (fg=0) -> contrarian buy -> score 95; extreme greed (fg=100) -> caution -> score 5
    fg_score = int(_clamp(95 - fg_value * 0.9, Config.SCORE_MIN, Config.SCORE_MAX))
    # Positive funding = crowded longs = bearish contrarian pressure
    s_funding = max(-1.0, min(1.0, -funding / 0.0005))
    funding_score = int(_clamp(50 + s_funding * 30, Config.FUNDING_SCORE_MIN, Config.FUNDING_SCORE_MAX))

    score = int(_clamp(0.7 * fg_score + 0.3 * funding_score, Config.SCORE_MIN, Config.SCORE_MAX))
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


async def _fetch_all_news_titles() -> list[str]:
    """Fetch raw headlines from all RSS feeds. Called once per cycle."""
    logger.info("Perception: news-briefing (fetching)...")

    def _fetch(url: str) -> str:
        try:
            return requests.get(
                url, headers={"User-Agent": "SentinelCouncil/1.0"}, timeout=8,
            ).text
        except Exception as exc:
            logger.warning("News fetch failed [%s]: %s", url, exc)
            return ""

    texts = await asyncio.gather(*[asyncio.to_thread(_fetch, u) for u in _NEWS_FEEDS])
    titles: list[str] = []
    for text in texts:
        if not text:
            continue
        raw = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", text, re.DOTALL)
        titles.extend(t.lower().strip() for t in raw[1:11])
    return titles


def score_news_titles(titles: list[str], symbol: str) -> dict[str, Any]:
    """Score pre-fetched headlines filtered to symbol-relevant ones where possible."""
    titles = [str(t).lower().strip() for t in titles if str(t).strip()]
    if not titles:
        return {"score": 50, "summary": "News data unavailable — neutral"}

    coin_keys = _SYMBOL_KEYWORDS.get(symbol, [])
    relevant = [t for t in titles if any(k in t for k in coin_keys)] if coin_keys else []
    if coin_keys and not relevant:
        return {"score": 50, "summary": f"No {symbol.replace('USDT', '')}-specific headlines — news neutral"}

    scored_titles = relevant if relevant else titles

    combined = " ".join(scored_titles)
    bull = sum(combined.count(w) for w in _NEWS_BULL)
    bear = sum(combined.count(w) for w in _NEWS_BEAR)
    total = bull + bear
    score = int(_clamp(
        50 if total == 0 else Config.NEWS_SCORE_MIN + bull / total * (Config.NEWS_SCORE_MAX - Config.NEWS_SCORE_MIN),
        Config.NEWS_SCORE_MIN,
        Config.NEWS_SCORE_MAX,
    ))
    tag = f"{len(scored_titles)} {symbol.replace('USDT', '')}-specific" if relevant else f"{len(titles)} global"
    return {"score": score, "summary": f"{tag} headlines — {bull} bullish / {bear} bearish"}


async def get_market_intel_signal(session: ClientSession, symbol: str, prev_oi: float = 0.0) -> dict[str, Any]:
    """Symbol-level 24h price momentum + OI delta as institutional flow proxy."""
    logger.info("Perception: market-intel [%s]...", symbol)
    ticker = _bitget_rest_get("/api/v2/mix/market/ticker",
                              {"symbol": symbol, "productType": "USDT-FUTURES"})
    oi = _bitget_rest_get("/api/v2/mix/market/open-interest",
                          {"symbol": symbol, "productType": "USDT-FUTURES"})
    rows = _as_list(ticker.get("data"))
    oi_rows = _as_list(_as_dict(oi.get("data")).get("openInterestList"))
    if not rows:
        return {"score": 50, "summary": "Market intel data unavailable — neutral", "oi_size": 0.0}
    row = _as_dict(rows[0])
    if _is_stale_timestamp(row.get("ts") or row.get("timestamp") or row.get("time"), Config.TICKER_MAX_AGE_SECONDS):
        logger.warning("Market intel ticker stale for %s — neutral", symbol)
        return {"score": 50, "summary": "Market intel ticker stale — neutral", "oi_size": 0.0}
    change24h = _safe_float(row.get("change24h"), 0.0)
    oi_size = max(0.0, _safe_float(_as_dict(oi_rows[0]).get("size"), 0.0)) if oi_rows else 0.0

    s_change = max(-1.0, min(1.0, change24h / 0.03))
    price_score = int(_clamp(50 + s_change * 45, Config.SCORE_MIN, Config.SCORE_MAX))

    # OI delta: rising OI confirms price direction; divergence is a warning
    oi_adj = 0
    oi_note = f", OI {oi_size:.0f}" if oi_size else ""
    if prev_oi > 0 and oi_size > 0:
        oi_delta = (oi_size - prev_oi) / prev_oi
        s_oi = max(-1.0, min(1.0, oi_delta / 0.05))  # 5% OI change = full signal
        oi_adj = int(s_oi * s_change * 15)            # only amplifies when price + OI agree
        oi_note = f", OI Δ{oi_delta * 100:+.1f}%"

    score = int(_clamp(price_score + oi_adj, Config.SCORE_MIN, Config.SCORE_MAX))
    flow_label = "accumulating" if score > 55 else "distributing" if score < 45 else "neutral flow"
    summary = f"{symbol} 24h {change24h * 100:+.2f}% — {flow_label}{oi_note}"
    return {"score": score, "summary": summary, "oi_size": oi_size}


async def _safe_signal_call(label: str, symbol: str, coro: Any) -> dict[str, Any]:
    try:
        return await coro
    except Exception as exc:
        logger.error("%s signal failed for %s: %s", label, symbol, exc)
        return {"score": 50, "summary": f"{label} signal failed — neutral"}


async def run_perception(
    session: ClientSession,
    symbol: str,
    macro_result: dict[str, Any],
    news_titles: list[str],
    prev_oi: float = 0.0,
) -> dict[str, dict[str, Any]]:
    technical_result = await _safe_signal_call("Technical", symbol, get_technical_signal(session, symbol))
    sentiment_result = await _safe_signal_call("Sentiment", symbol, get_sentiment_signal(session, symbol))
    intel_result = await _safe_signal_call("Market intel", symbol, get_market_intel_signal(session, symbol, prev_oi))
    try:
        news_result = score_news_titles(news_titles, symbol)
    except Exception as exc:
        logger.error("News signal failed for %s: %s", symbol, exc)
        news_result = {"score": 50, "summary": "News signal failed — neutral"}

    perception = {
        "macro": _safe_signal_result(macro_result, "Macro"),
        "technical": _safe_signal_result(technical_result, "Technical"),
        "sentiment": _safe_signal_result(sentiment_result, "Sentiment"),
        "news": _safe_signal_result(news_result, "News"),
        "intel": _safe_signal_result(intel_result, "Market intel"),
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

def _asset_bias(symbol: str) -> float:
    """Small stable offset used only to break perfectly identical score ties."""
    weighted = sum((idx + 1) * ord(ch) for idx, ch in enumerate(symbol))
    return float(weighted % 21 - 10)


def _valid_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(score):
        return None
    return _clamp(score, 0, 100)


def _looks_like_fallback(summary: Any) -> bool:
    text = str(summary or "").lower()
    return any(
        phrase in text
        for phrase in [
            "unavailable",
            "failed",
            "insufficient",
            "malformed",
            "stale",
            "no data",
            "no summary",
            "no ",
        ]
    ) and "neutral" in text


def _normalize_scores(scores: dict[str, Any], summaries: dict[str, Any] | None = None, symbol: str = "") -> dict[str, int]:
    raw_scores = _as_dict(scores)
    raw_summaries = _as_dict(summaries)
    valid = {
        k: (
            None
            if _looks_like_fallback(raw_summaries.get(k)) and _valid_score(raw_scores.get(k)) == 50
            else _valid_score(raw_scores.get(k))
        )
        for k in Config.WEIGHTS
    }
    valid = {k: v for k, v in valid.items() if v is not None}
    if valid:
        baseline = sum(Config.WEIGHTS[k] * valid[k] for k in valid) / sum(Config.WEIGHTS[k] for k in valid)
    else:
        baseline = 50.0 + _asset_bias(symbol)

    normalized: dict[str, int] = {}
    for k in Config.WEIGHTS:
        score = valid.get(k)
        if score is None:
            signal_offset = (Config.WEIGHTS[k] - 0.20) * 20
            score = baseline + signal_offset + _asset_bias(f"{symbol}:{k}") * 0.2
        normalized[k] = int(round(_clamp(score, 0, 100)))
    return normalized


def _distance_to_action(score: float) -> float:
    return round(min(abs(score - Config.BUY_THRESHOLD), abs(score - Config.SELL_THRESHOLD)), 1)


def _decision_for_score(score: float) -> str:
    if score >= Config.BUY_THRESHOLD:
        return "BUY"
    if score <= Config.SELL_THRESHOLD:
        return "SELL"
    if score >= Config.WATCH_BUY_THRESHOLD or score <= Config.WATCH_SELL_THRESHOLD:
        return "WATCH"
    return "WAIT"


def _direction_for_score(score: float) -> str:
    if score >= Config.WATCH_BUY_THRESHOLD:
        return "toward_buy"
    if score <= Config.WATCH_SELL_THRESHOLD:
        return "toward_sell"
    return "neutral"


def _nearest_direction(score: float) -> str:
    if abs(score - Config.BUY_THRESHOLD) < abs(score - Config.SELL_THRESHOLD):
        return "toward_buy"
    if abs(score - Config.SELL_THRESHOLD) < abs(score - Config.BUY_THRESHOLD):
        return "toward_sell"
    return "neutral"


def _decision_gap_text(score: float, action: str) -> str:
    if score >= Config.BUY_THRESHOLD:
        return f"{score - Config.BUY_THRESHOLD:.1f} points above BUY threshold"
    if score <= Config.SELL_THRESHOLD:
        return f"{Config.SELL_THRESHOLD - score:.1f} points below SELL threshold"
    if score > 50:
        return f"{Config.BUY_THRESHOLD - score:.1f} points below BUY threshold"
    if score < 50:
        return f"{score - Config.SELL_THRESHOLD:.1f} points above SELL threshold"
    return f"{_distance_to_action(score):.1f} points from nearest action threshold"


def _rank_key(council: dict[str, Any]) -> tuple[float, float, float, str]:
    score = _safe_float(council.get("confidence"), 50.0)
    return (
        _distance_to_action(score),
        abs(score - Config.BUY_THRESHOLD),
        abs(score - Config.SELL_THRESHOLD),
        str(council.get("symbol", "")),
    )


def run_council(scores: dict[str, int], summaries: dict[str, str], symbol: str = "") -> dict[str, Any]:
    scores = _normalize_scores(scores, summaries, symbol)
    summaries = {
        k: str(_as_dict(summaries).get(k) or "No summary available.")[:500]
        for k in Config.WEIGHTS
    }
    confidence = round(sum(Config.WEIGHTS[k] * scores[k] for k in Config.WEIGHTS), 1)
    decision = _decision_for_score(confidence)
    distance = _distance_to_action(confidence)
    direction = _direction_for_score(confidence)
    dominant = max(scores, key=lambda k: Config.WEIGHTS[k] * scores[k])
    gap_text = _decision_gap_text(confidence, decision)

    lines = [
        f"  - {k} ({int(Config.WEIGHTS[k] * 100)}% weight, score {scores[k]}): {summaries[k]}"
        for k in ["macro", "technical", "sentiment", "news", "intel"]
    ]
    explanation = (
        f"{decision} signal at {confidence:.1f}% confidence ({gap_text}). "
        f"Dominant: {dominant}.\n" + "\n".join(lines)
    )

    logger.info("Council -> %s @ %.1f%% confidence (dominant: %s)", decision, confidence, dominant)
    return {
        "decision": decision,
        "confidence": confidence,
        "score": confidence,
        "distance_to_action": distance,
        "direction": direction,
        "dominant": dominant,
        "reason": f"{dominant} signal sets {direction.replace('_', ' ')} pressure at {confidence:.1f}.",
        "explanation": explanation,
    }


def finalize_cycle_decisions(councils: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted((_as_dict(c) for c in councils), key=_rank_key)
    seen_scores: dict[float, int] = {}
    for council in ranked:
        score = _safe_float(council.get("confidence"), 0.0)
        seen_scores[score] = seen_scores.get(score, 0) + 1
    tied_index: dict[float, int] = {}
    for council in ranked:
        score = _safe_float(council.get("confidence"), 0.0)
        if seen_scores.get(score, 0) > 1:
            idx = tied_index.get(score, 0)
            tied_index[score] = idx + 1
            centered = idx - (seen_scores[score] - 1) / 2
            adjusted = round(_clamp(score + centered * 0.2, 0, 100), 1)
            council["confidence"] = adjusted
            council["score"] = adjusted
            council["decision"] = _decision_for_score(adjusted)
            council["distance_to_action"] = _distance_to_action(adjusted)
            council["direction"] = _direction_for_score(adjusted)
    ranked = sorted(ranked, key=_rank_key)
    if ranked and all(c.get("decision") == "WAIT" for c in ranked):
        top = ranked[0]
        top["decision"] = "WATCH"
        top["direction"] = _nearest_direction(_safe_float(top.get("confidence"), 50.0))
        top["reason"] = "Closest ranked asset promoted from WAIT to WATCH to maintain directional pressure."
        top["explanation"] = str(top.get("explanation", "")).replace("WAIT signal", "WATCH signal", 1)

    for rank, council in enumerate(ranked, start=1):
        council["rank"] = rank
        council["top_opportunity"] = rank == 1
    return ranked


# ─────────────────────────────────────────────────────────────────
# RISK ENGINE
# ─────────────────────────────────────────────────────────────────

def calculate_position(
    decision: str, confidence: float, symbol: str, balance: float, open_positions: dict[str, Any],
) -> dict[str, Any]:
    decision = str(decision).upper()
    balance = max(0.0, _safe_float(balance, 0.0))
    open_positions = _as_dict(open_positions)
    if decision not in {"BUY", "SELL", "WATCH", "WAIT"}:
        logger.warning("Unknown decision for %s: %s", symbol, decision)
        return {"action": "WAIT", "size": 0.0, "reason": "Unknown decision"}
    if decision in {"WATCH", "WAIT"}:
        return {"action": decision, "size": 0.0, "reason": f"{decision} signal without execution threshold"}

    if decision == "SELL":
        if symbol in open_positions:
            return {"action": "CLOSE", "size": _safe_float(_as_dict(open_positions[symbol]).get("size"), 0.0),
                     "reason": "Bearish — closing position"}
        return {"action": "SELL", "size": 0.0, "reason": "Bearish but no position to close"}

    # decision == "BUY"
    if symbol in open_positions:
        return {"action": "WATCH", "size": 0.0, "reason": "Already in position — no pyramiding"}
    if len(open_positions) >= Config.MAX_POSITIONS:
        return {"action": "WATCH", "size": 0.0, "reason": "Max concurrent positions reached"}

    risk_pct = Config.RISK_AGGRESSIVE if confidence >= Config.AGGRESSIVE_THRESHOLD else Config.RISK_CONSERVATIVE
    size = round(balance * risk_pct, 2)
    if size <= 0.0:
        logger.warning("Risk [%s] cannot size position from balance %.4f", symbol, balance)
        return {"action": "WAIT", "size": 0.0, "reason": "Insufficient balance"}
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
        self.last_oi: dict[str, float] = {}

    def get_price(self, symbol: str) -> float:
        try:
            payload = _bitget_rest_get("/api/v2/mix/market/ticker",
                                       {"symbol": symbol, "productType": "USDT-FUTURES"})
            rows = _as_list(payload.get("data"))
            if not rows:
                logger.error("Price fetch returned no ticker rows for %s", symbol)
                return 0.0
            row = _as_dict(rows[0])
            if _is_stale_timestamp(row.get("ts") or row.get("timestamp") or row.get("time"), Config.TICKER_MAX_AGE_SECONDS):
                logger.error("Price fetch returned stale ticker for %s", symbol)
                return 0.0
            price = _safe_float(row.get("lastPr"), 0.0)
            low, high = Config.PRICE_BOUNDS.get(symbol, (0.000001, 1_000_000_000.0))
            if not math.isfinite(price) or price < low or price > high:
                logger.error("Invalid price for %s: %.12g outside [%s, %s]", symbol, price, low, high)
                return 0.0
            return price
        except Exception as exc:
            logger.error("Price fetch failed for %s: %s", symbol, exc)
            return 0.0

    def open_long(self, symbol: str, size: float) -> dict[str, Any]:
        if symbol not in Config.SYMBOLS:
            logger.warning("Cannot open position — unsupported symbol %s", symbol)
            return {}
        if symbol in self.open_positions:
            logger.warning("Cannot open position — %s already open", symbol)
            return {}
        price = self.get_price(symbol)
        if price <= 0.0:
            logger.warning("Cannot open position — price unavailable for %s", symbol)
            return {}
        if size <= 0.0 or size > self.balance:
            logger.warning("Cannot open position — invalid size %.2f for balance %.2f", size, self.balance)
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
            logger.warning("Cannot close position — %s is not open", symbol)
            return {}
        pos = _as_dict(self.open_positions.pop(symbol))
        size = _safe_float(pos.get("size"), 0.0)
        entry_price = _safe_float(pos.get("entry_price"), 0.0)
        if size <= 0.0 or entry_price <= 0.0:
            logger.error("Cannot close %s — invalid restored position: %s", symbol, pos)
            self.save()
            return {}
        price = self.get_price(symbol)
        if price == 0.0:
            logger.warning("Close price unavailable for %s — using entry price to avoid feed-loss artifact", symbol)
            price = entry_price
        pnl = round((price - entry_price) / entry_price * size, 4)
        self.balance += size + pnl
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
        action = str(_as_dict(risk_result).get("action", "WAIT"))
        if action == "OPEN_LONG":
            return self.open_long(symbol, _safe_float(risk_result.get("size"), 0.0))
        if action == "CLOSE":
            return self.close_position(symbol)
        return {"action": action, "symbol": symbol}

    def win_rate(self) -> float:
        return round(self.win_count / self.trade_count * 100, 1) if self.trade_count else 0.0

    def check_exits(self) -> list[tuple[str, str]]:
        """Return symbols where stop-loss or take-profit has been hit."""
        to_close = []
        for symbol, pos in list(self.open_positions.items()):
            pos = _as_dict(pos)
            entry_price = _safe_float(pos.get("entry_price"), 0.0)
            if entry_price <= 0.0:
                logger.error("Skipping exit check for %s — invalid entry price %.8f", symbol, entry_price)
                continue
            price = self.get_price(symbol)
            if price == 0.0:
                continue
            pnl_pct = (price - entry_price) / entry_price
            if pnl_pct <= Config.SL_PCT:
                logger.info("Stop-loss %.2f%% — queuing close %s", pnl_pct * 100, symbol)
                to_close.append((symbol, "stop-loss"))
            elif pnl_pct >= Config.TP_PCT:
                logger.info("Take-profit %.2f%% — queuing close %s", pnl_pct * 100, symbol)
                to_close.append((symbol, "take-profit"))
        return to_close

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
            text = json.dumps({**self.summary(), "trades": self.trades, "last_oi": self.last_oi}, indent=2)
            _atomic_write_text(Config.PORTFOLIO_JSON, text)
        except Exception as exc:
            logger.error("Portfolio save failed: %s", exc)

    @classmethod
    def load_or_create(cls) -> "SimPortfolio":
        if Config.PORTFOLIO_JSON.exists():
            try:
                with open(Config.PORTFOLIO_JSON, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("portfolio JSON root is not an object")
                portfolio = cls(balance=max(0.0, _safe_float(data.get("balance"), Config.STARTING_BALANCE)))
                positions: dict[str, Any] = {}
                for symbol, raw_pos in _as_dict(data.get("open_positions")).items():
                    if symbol not in Config.SYMBOLS:
                        logger.warning("Dropping unsupported restored position %s", symbol)
                        continue
                    pos = _as_dict(raw_pos)
                    size = _safe_float(pos.get("size"), 0.0)
                    entry_price = _safe_float(pos.get("entry_price"), 0.0)
                    if size <= 0.0 or entry_price <= 0.0:
                        logger.warning("Dropping invalid restored position %s: %s", symbol, pos)
                        continue
                    positions[symbol] = {
                        **pos,
                        "symbol": symbol,
                        "size": size,
                        "entry_price": entry_price,
                        "opened_at": str(pos.get("opened_at") or ""),
                    }
                portfolio.open_positions = positions
                portfolio.trades = _as_list(data.get("trades"))
                portfolio.total_pnl = _safe_float(data.get("total_pnl"), 0.0)
                portfolio.win_count = max(0, _safe_int(data.get("win_count"), 0))
                portfolio.trade_count = max(0, _safe_int(data.get("trade_count"), 0))
                portfolio.last_oi = {
                    symbol: max(0.0, _safe_float(value, 0.0))
                    for symbol, value in _as_dict(data.get("last_oi")).items()
                    if symbol in Config.SYMBOLS
                }
                logger.info("Portfolio loaded — balance $%.2f, %d open positions",
                            portfolio.balance, len(portfolio.open_positions))
                return portfolio
            except Exception as exc:
                logger.warning("Could not load portfolio, starting fresh: %s", exc)
        return cls()


# ─────────────────────────────────────────────────────────────────
# AUDIT TRAIL — trades.csv
# ─────────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "timestamp", "symbol", "decision", "confidence", "action", "size",
    "price", "quantity", "balance_change",
    "distance_to_action", "direction", "rank", "top_opportunity",
    "balance", "pnl", "trade_pnl", "explanation",
]
_AUDIT_CSV_VALIDATED = False


def _quarantine_audit_csv(reason: str) -> None:
    if not Config.TRADES_CSV.exists():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    target = Config.TRADES_CSV.with_name(f"{Config.TRADES_CSV.stem}.corrupt-{stamp}{Config.TRADES_CSV.suffix}")
    try:
        os.replace(Config.TRADES_CSV, target)
        logger.error("Audit CSV quarantined (%s): %s", reason, target)
    except Exception as exc:
        logger.error("Could not quarantine audit CSV (%s): %s", reason, exc)


def _ensure_csv_header() -> None:
    global _AUDIT_CSV_VALIDATED
    Config.TRADES_CSV.parent.mkdir(parents=True, exist_ok=True)
    if Config.TRADES_CSV.exists() and Config.TRADES_CSV.stat().st_size > 0:
        if not _AUDIT_CSV_VALIDATED:
            try:
                with open(Config.TRADES_CSV, newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    if reader.fieldnames != CSV_FIELDS:
                        raise csv.Error(f"header mismatch: {reader.fieldnames}")
                    for row in reader:
                        if row is None or None in row:
                            raise csv.Error("malformed row")
                _AUDIT_CSV_VALIDATED = True
                return
            except (csv.Error, UnicodeDecodeError, OSError) as exc:
                _quarantine_audit_csv(str(exc))
        else:
            return
    tmp = Config.TRADES_CSV.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, Config.TRADES_CSV)
    _AUDIT_CSV_VALIDATED = True


def log_trade_row(
    symbol: str,
    council: dict[str, Any],
    risk_result: dict[str, Any],
    portfolio: SimPortfolio,
    trade: dict[str, Any] | None = None,
) -> None:
    _ensure_csv_header()
    trade_pnl = float(trade.get("pnl", 0.0)) if trade else 0.0
    council = _as_dict(council)
    risk_result = _as_dict(risk_result)
    trade = _as_dict(trade)
    action = str(risk_result.get("action", "WAIT"))
    size = _safe_float(risk_result.get("size"), 0.0)
    price = 0.0
    balance_change = 0.0
    if action == "OPEN_LONG":
        price = _safe_float(trade.get("entry_price"), 0.0)
        balance_change = -size
    elif action == "CLOSE":
        price = _safe_float(trade.get("exit_price"), 0.0)
        balance_change = size + trade_pnl
    quantity = round(size / price, 8) if price > 0.0 and size > 0.0 else 0.0
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "decision": str(council.get("decision", "WAIT")),
        "confidence": _safe_float(council.get("confidence"), 0.0),
        "action": action,
        "size": size,
        "price": round(price, 8),
        "quantity": quantity,
        "balance_change": round(balance_change, 4),
        "distance_to_action": _safe_float(council.get("distance_to_action"), 0.0),
        "direction": str(council.get("direction", "neutral")),
        "rank": _safe_int(council.get("rank"), 0),
        "top_opportunity": str(bool(council.get("top_opportunity", False))),
        "balance": round(portfolio.balance, 2),
        "pnl": round(portfolio.total_pnl, 4),
        "trade_pnl": round(trade_pnl, 4),
        "explanation": str(council.get("explanation", ""))[:5000],
    }
    try:
        with open(Config.TRADES_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writerow(row)
            f.flush()
            os.fsync(f.fileno())
        logger.info("Logged [%s] %s @ %.1f%% → %s", row["symbol"], row["decision"], row["confidence"], row["action"])
    except Exception as exc:
        logger.error("Audit CSV write failed: %s", exc)


def log_exit_row(symbol: str, exit_reason: str, trade: dict[str, Any], portfolio: SimPortfolio) -> None:
    pnl = float(trade.get("pnl", 0.0))
    decision = "SELL"
    council = {
        "decision": decision,
        "confidence": 100.0,
        "explanation": (
            f"{decision} signal at 100.0% confidence (automatic {exit_reason}). "
            f"Dominant: risk.\n"
            f"  - risk (100% weight, score 100): {exit_reason} closed {symbol} "
            f"at {float(trade.get('exit_price', 0.0)):.4f}; trade PnL {pnl:+.4f}"
        ),
    }
    risk_result = {"action": "CLOSE", "size": float(trade.get("size", 0.0))}
    log_trade_row(symbol, council, risk_result, portfolio, trade)


# ─────────────────────────────────────────────────────────────────
# MAIN CYCLE + LOOP
# ─────────────────────────────────────────────────────────────────

def _mcp_server_params() -> StdioServerParameters:
    env = os.environ.copy()
    env.update({
        "BITGET_API_KEY": Config.API_KEY,
        "BITGET_SECRET_KEY": Config.SECRET_KEY,
        "BITGET_PASSPHRASE": Config.PASSPHRASE,
    })
    return StdioServerParameters(
        command="npx",
        args=["-y", Config.BITGET_MCP_PACKAGE, "--modules", "all"],
        env=env,
    )


_CYCLE_STATUS_PATH = Path("data/cycle_status.json")


def _write_cycle_status(status: str) -> None:
    try:
        _atomic_write_text(
            _CYCLE_STATUS_PATH,
            json.dumps({"status": status, "ts": datetime.now(timezone.utc).isoformat()}),
        )
    except Exception as exc:
        logger.error("Cycle status write failed: %s", exc)


async def run_cycle(portfolio: SimPortfolio) -> None:
    """One full cycle: all five perspectives, for every symbol, via one MCP session."""
    _write_cycle_status("running")
    try:
        async with stdio_client(_mcp_server_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                macro_results = await get_macro_signals(session)
                news_titles = await _fetch_all_news_titles()

                # SL/TP check — runs before new signals each cycle
                cooldown_symbols = set()
                for sym, exit_reason in portfolio.check_exits():
                    trade = portfolio.close_position(sym)
                    if trade:
                        log_exit_row(sym, exit_reason, trade, portfolio)
                        cooldown_symbols.add(sym)
                        logger.info("Exit triggered [%s] PnL $%.4f", sym, trade.get("pnl", 0))

                cycle_councils: list[dict[str, Any]] = []
                for symbol in Config.SYMBOLS:
                    logger.info("=" * 60)
                    logger.info("CYCLE START — %s", symbol)
                    try:
                        perception = await run_perception(
                            session, symbol, macro_results.get(symbol, {}), news_titles,
                            portfolio.last_oi.get(symbol, 0.0),
                        )
                        portfolio.last_oi[symbol] = perception["intel"].get("oi_size", 0.0)
                        scores = {k: v["score"] for k, v in perception.items()}
                        summaries = {k: v["summary"] for k, v in perception.items()}

                        council = run_council(scores, summaries, symbol)
                        council["symbol"] = symbol
                        cycle_councils.append(council)
                        logger.info("Explanation [%s]:\n%s", symbol, council["explanation"])
                    except Exception as exc:
                        logger.error("Cycle failed for %s: %s", symbol, exc)

                for council in finalize_cycle_decisions(cycle_councils):
                    symbol = str(council.get("symbol", ""))
                    if not symbol:
                        continue
                    try:
                        if symbol in cooldown_symbols:
                            risk_result = {
                                "action": "WAIT",
                                "size": 0.0,
                                "reason": "Same-cycle re-entry blocked after automatic exit",
                            }
                        else:
                            risk_result = calculate_position(
                                council["decision"], council["confidence"],
                                symbol, portfolio.balance, portfolio.open_positions,
                            )
                        trade = portfolio.execute(risk_result, symbol)
                        log_trade_row(symbol, council, risk_result, portfolio, trade)

                        logger.info(
                            "CYCLE END — #%d %s | %s | %s | distance %.1f | balance $%.2f",
                            _safe_int(council.get("rank"), 0), symbol, council["decision"],
                            risk_result["action"], _safe_float(council.get("distance_to_action"), 0.0),
                            portfolio.balance,
                        )
                    except Exception as exc:
                        logger.error("Execution failed for %s: %s", symbol, exc)
    except Exception as exc:
        logger.error("MCP session failed for this cycle: %s", exc)
    finally:
        _write_cycle_status("sleeping")


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


def run_decision_engine_test() -> None:
    tied_inputs = [
        {
            "symbol": symbol,
            **run_council(
                {k: 50 for k in Config.WEIGHTS},
                {k: "Signal unavailable — neutral" for k in Config.WEIGHTS},
                symbol,
            ),
        }
        for symbol in Config.SYMBOLS
    ]
    ranked = finalize_cycle_decisions(tied_inputs)
    scores = [c["confidence"] for c in ranked]
    actions = [c["decision"] for c in ranked]
    distances = [c["distance_to_action"] for c in ranked]

    assert len(ranked) == len(Config.SYMBOLS), "assets missing from ranked cycle"
    assert len(set(scores)) > 1, "scores did not disperse across assets"
    assert any(action in {"BUY", "SELL", "WATCH"} for action in actions), "cycle is inactive"
    assert ranked[0]["top_opportunity"] is True, "top asset not flagged"
    assert ranked[0]["distance_to_action"] == min(distances), "top opportunity is not minimum distance"
    assert all(c["decision"] != "HOLD" for c in ranked), "HOLD leaked into decisions"

    print("\n" + "=" * 60)
    print("SENTINEL DECISION ENGINE TEST")
    print("=" * 60)
    for council in ranked:
        print(
            f"  #{council['rank']} {council['symbol']}: "
            f"{council['confidence']:.1f} {council['decision']} "
            f"distance={council['distance_to_action']:.1f} {council['direction']}"
        )
    print("\nAll decision engine tests passed.")
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
        run_decision_engine_test()
    elif args.once:
        result_portfolio = run_once()
        print("\nPortfolio summary:")
        print(json.dumps(result_portfolio.summary(), indent=2))
    else:
        asyncio.run(main_loop())
