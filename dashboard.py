"""
Sentinel Council — Trading Agent Dashboard
Mission Control aesthetic: phosphor displays, monospace readouts, dark terminal.
Run with: python3.12 -m streamlit run dashboard.py
"""

import html
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import requests
import streamlit as st

TRADES_CSV = Path("trades.csv")
PORTFOLIO_JSON = Path("data/portfolio.json")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
SYM_LABELS = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL"}
BUY_THRESHOLD = 58.0
SELL_THRESHOLD = 42.0
STARTING_BALANCE = 10_000.0
CYCLE_SECONDS = 3600
REFRESH_SECONDS = 60
NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://feeds.feedburner.com/CoinDesk",
    "https://cryptobriefing.com/feed/",
    "https://decrypt.co/feed",
]

EXPLANATION_LINE_RE = re.compile(
    r"^\s*-\s*(?P<skill>\w+) \((?P<weight>\d+)% weight, score (?P<score>\d+)\):\s*(?P<summary>.*)$"
)
DOMINANT_RE = re.compile(r"Dominant:\s*(\w+)")

# ─── CSS ──────────────────────────────────────────────────────────

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Chakra+Petch:ital,wght@0,300;0,400;0,600;0,700;1,300&family=Share+Tech+Mono&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500&display=swap');

[data-testid="stAppViewContainer"] {
    background: #070b0f !important;
    background-image:
        radial-gradient(ellipse at 15% 10%, rgba(0,255,135,0.025) 0%, transparent 55%),
        radial-gradient(ellipse at 85% 85%, rgba(77,158,255,0.02) 0%, transparent 55%) !important;
}
[data-testid="stHeader"] { background: transparent !important; border-bottom: none !important; }
section[data-testid="stSidebar"] { display: none !important; }
.block-container { padding-top: 2.5rem !important; padding-bottom: 60px !important; max-width: 1400px !important; }
footer { display: none !important; }
*, *::before, *::after { box-sizing: border-box; }
p, li, span, div { font-family: 'DM Sans', sans-serif !important; }

/* ── Masthead ── */
.sc-masthead { display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid rgba(0,255,135,0.15);padding-bottom:1.25rem;margin-bottom:1.5rem; }
.sc-wordmark { font-family:'Chakra Petch',monospace !important;font-size:2.2rem;font-weight:700;letter-spacing:0.14em;color:#dce8f5;line-height:1;margin:0; }
.sc-wordmark em { color:#00ff87;font-style:normal; }
.sc-tagline { font-family:'Share Tech Mono',monospace !important;font-size:0.62rem;color:#2a3d52;letter-spacing:0.22em;margin-top:0.45rem; }
.sc-live-block { text-align:right; }
.sc-live { font-family:'Share Tech Mono',monospace !important;font-size:0.72rem;color:#00ff87;letter-spacing:0.18em;display:inline-flex;align-items:center;gap:0.45rem; }
.pulse-dot { width:7px;height:7px;background:#00ff87;border-radius:50%;display:inline-block;box-shadow:0 0 7px #00ff87;animation:pulse-anim 1.6s ease-in-out infinite; }
@keyframes pulse-anim { 0%,100%{opacity:1;box-shadow:0 0 7px #00ff87} 50%{opacity:.3;box-shadow:0 0 2px #00ff87} }
.sc-clock { font-family:'Share Tech Mono',monospace !important;font-size:0.6rem;color:#2a3d52;letter-spacing:0.12em;margin-top:0.3rem; }
.sc-countdown { font-family:'Share Tech Mono',monospace !important;font-size:0.62rem;color:#f5a623;letter-spacing:0.12em;margin-top:0.2rem; }

/* ── Price Strip ── */
.price-strip { display:grid;grid-template-columns:repeat(3,1fr);gap:0.7rem;margin-bottom:1.5rem; }
.price-card { background:#0a1218;border:1px solid rgba(0,255,135,0.08);border-radius:3px;padding:0.75rem 1.1rem;display:flex;justify-content:space-between;align-items:center;transition:border-color .2s; }
.price-card:hover { border-color:rgba(0,255,135,0.2); }
.price-sym { font-family:'Chakra Petch',monospace !important;font-size:0.72rem;color:#2a3d52;letter-spacing:0.15em; }
.price-val { font-family:'Share Tech Mono',monospace !important;font-size:1.05rem;color:#c8d8ea;line-height:1;margin-top:0.15rem; }
.price-chg { font-family:'Share Tech Mono',monospace !important;font-size:0.72rem;letter-spacing:0.05em;text-align:right; }

/* ── Section Labels ── */
.sc-section { font-family:'Share Tech Mono',monospace !important;font-size:0.62rem;letter-spacing:0.28em;color:#2a3d52;text-transform:uppercase;border-bottom:1px solid #0c1620;padding-bottom:0.4rem;margin:2rem 0 1rem; }

/* ── Metric Cards ── */
.metric-row { display:grid;grid-template-columns:repeat(4,1fr);gap:0.9rem;margin-bottom:0.5rem; }
.metric-card { background:#0d1520;border:1px solid rgba(0,255,135,0.1);border-radius:3px;padding:1.1rem 1.4rem;position:relative;overflow:hidden;transition:border-color .25s; }
.metric-card:hover { border-color:rgba(0,255,135,0.25); }
.metric-card::after { content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(0,255,135,.45),transparent); }
.metric-label { font-family:'Share Tech Mono',monospace !important;font-size:0.6rem;color:#2a3d52;letter-spacing:0.2em;margin-bottom:0.55rem; }
.metric-value { font-family:'Share Tech Mono',monospace !important;font-size:1.7rem;color:#c8d8ea;line-height:1;font-weight:400; }
.metric-value.pos { color:#00ff87; }
.metric-value.neg { color:#ff4d4d; }

/* ── Signal Grid ── */
.signal-grid { display:grid;grid-template-columns:repeat(3,1fr);gap:0.9rem;margin-bottom:0.5rem; }
.signal-card { background:#0d1520;border:1px solid rgba(0,255,135,0.1);border-radius:3px;padding:1.2rem 1.4rem 1.3rem;transition:border-color .25s; }
.signal-card:hover { border-color:rgba(0,255,135,0.22); }
.signal-sym { font-family:'Share Tech Mono',monospace !important;font-size:0.62rem;color:#2a3d52;letter-spacing:0.2em;margin-bottom:0.6rem; }
.signal-sym em { color:#f5a623;font-style:normal; }
.sig-buy  { font-family:'Chakra Petch',monospace !important;font-size:1.9rem;font-weight:700;color:#00ff87;letter-spacing:.12em;text-shadow:0 0 22px rgba(0,255,135,.35); }
.sig-sell { font-family:'Chakra Petch',monospace !important;font-size:1.9rem;font-weight:700;color:#ff4d4d;letter-spacing:.12em;text-shadow:0 0 22px rgba(255,77,77,.35); }
.sig-hold { font-family:'Chakra Petch',monospace !important;font-size:1.9rem;font-weight:700;color:#2a3d52;letter-spacing:.12em; }
.sig-conf { font-family:'Share Tech Mono',monospace !important;font-size:0.78rem;margin-top:0.2rem;margin-bottom:0.7rem; }
.sig-dom  { display:inline-block;font-family:'Share Tech Mono',monospace !important;font-size:0.58rem;letter-spacing:.12em;color:#f5a623;border:1px solid rgba(245,166,35,.25);border-radius:2px;padding:0.1rem 0.4rem;margin-bottom:0.9rem; }
.gauge-wrap { display:flex;justify-content:center;margin:0.3rem 0 0.7rem; }

/* ── Score Bars ── */
.score-bars { display:flex;flex-direction:column;gap:0.55rem; }
.score-row  { display:flex;align-items:center;gap:0.55rem; }
.score-skill { font-family:'Share Tech Mono',monospace !important;font-size:0.58rem;color:#2a3d52;width:72px;letter-spacing:.08em;flex-shrink:0; }
.score-track { flex:1;height:3px;background:rgba(255,255,255,.05);border-radius:2px;overflow:hidden; }
.score-fill  { height:100%;border-radius:2px; }
.score-num   { font-family:'Share Tech Mono',monospace !important;font-size:0.68rem;width:22px;text-align:right;flex-shrink:0; }

/* ── Breakdown ── */
.bd-item { margin-bottom:1.1rem; }
.bd-header { display:flex;justify-content:space-between;margin-bottom:.28rem; }
.bd-skill  { font-family:'Share Tech Mono',monospace !important;font-size:.62rem;color:#2a3d52;letter-spacing:.12em; }
.bd-score  { font-family:'Share Tech Mono',monospace !important;font-size:.88rem;font-weight:600; }
.bd-track  { height:5px;background:rgba(255,255,255,.04);border-radius:3px;overflow:hidden; }
.bd-fill   { height:100%;border-radius:3px; }
.bd-weight { font-family:'DM Sans',sans-serif !important;font-size:.62rem;color:#2a3d52;margin-top:.2rem; }
.summary-row { padding:.7rem 1rem;background:#0a1218;border-radius:3px;margin-bottom:.45rem;border-left:2px solid; }
.summary-tag  { font-family:'Share Tech Mono',monospace !important;font-size:.58rem;letter-spacing:.12em;margin-bottom:.25rem; }
.summary-text { font-family:'DM Sans',sans-serif !important;font-size:.8rem;color:#5e7a94; }

/* ── Audit Table ── */
.audit-wrap  { overflow-x:auto; }
.audit-table { width:100%;border-collapse:collapse; }
.audit-table th { font-family:'Share Tech Mono',monospace !important;font-size:.58rem;color:#2a3d52;letter-spacing:.18em;text-align:left;padding:.45rem .7rem;border-bottom:1px solid #0c1620;white-space:nowrap; }
.audit-table td { font-family:'Share Tech Mono',monospace !important;font-size:.7rem;color:#5e7a94;padding:.45rem .7rem;border-bottom:1px solid rgba(255,255,255,.025);white-space:nowrap; }
.audit-table tr:hover td { background:rgba(0,255,135,.018); }
.d-buy  { color:#00ff87 !important;font-weight:600; }
.d-sell { color:#ff4d4d !important;font-weight:600; }
.d-hold { color:#2a3d52 !important; }
.ts-dim { color:#2a3d52 !important;font-size:.65rem !important; }

/* ── News grid ── */
.news-card { padding:.55rem .9rem;background:#0a1218;border-left:2px solid rgba(245,166,35,.3);border-radius:0 3px 3px 0;margin-bottom:.45rem; }
.news-text  { font-family:'DM Sans',sans-serif !important;font-size:.78rem;color:#7a8fa6;line-height:1.4; }

/* ── Ticker ── */
.ticker-wrap { position:fixed;bottom:0;left:0;right:0;height:30px;background:#070b0f;border-top:1px solid rgba(0,255,135,0.12);display:flex;align-items:center;z-index:9999;overflow:hidden; }
.ticker-badge { font-family:'Share Tech Mono',monospace;font-size:.6rem;color:#00ff87;letter-spacing:.15em;padding:0 .9rem;border-right:1px solid rgba(0,255,135,.2);height:100%;display:flex;align-items:center;white-space:nowrap;flex-shrink:0; }
.ticker-track { flex:1;overflow:hidden;position:relative; }
.ticker-inner { display:inline-block;white-space:nowrap;animation:ticker-scroll 80s linear infinite;font-family:'Share Tech Mono',monospace;font-size:.62rem;color:#3d5166;letter-spacing:.05em; }
@keyframes ticker-scroll { 0%{transform:translateX(0)} 100%{transform:translateX(-50%)} }
.ticker-inner em { color:#5e7a94;font-style:normal; }

/* ── Empty state ── */
.sc-empty { background:#0d1520;border:1px solid rgba(0,255,135,.1);border-radius:3px;padding:3rem;text-align:center;font-family:'Share Tech Mono',monospace;font-size:.75rem;color:#2a3d52;letter-spacing:.12em;line-height:2; }
.sc-empty code { color:#00ff87;background:none;font-family:inherit; }

/* ── Footer ── */
.sc-footer { font-family:'Share Tech Mono',monospace !important;font-size:.58rem;color:#1a2a38;letter-spacing:.14em;margin-top:3rem;padding-top:1rem;border-top:1px solid #0c1620;display:flex;justify-content:space-between; }

/* ── Streamlit overrides ── */
div[data-testid="stTabs"] > div > div > button { font-family:'Share Tech Mono',monospace !important;font-size:.68rem !important;letter-spacing:.1em !important;color:#2a3d52 !important;background:transparent !important; }
div[data-testid="stTabs"] > div > div > button[aria-selected="true"] { color:#00ff87 !important;border-bottom-color:#00ff87 !important; }
div[data-testid="stDownloadButton"] > button { font-family:'Share Tech Mono',monospace !important;font-size:.62rem !important;letter-spacing:.12em !important;background:transparent !important;color:#2a3d52 !important;border:1px solid #1a2a38 !important;border-radius:2px !important;padding:.35rem .9rem !important; }
div[data-testid="stDownloadButton"] > button:hover { color:#00ff87 !important;border-color:rgba(0,255,135,.3) !important; }
</style>
"""

# ─── Data Helpers ─────────────────────────────────────────────────

def load_trades() -> pd.DataFrame | None:
    if not TRADES_CSV.exists():
        return None
    try:
        df = pd.read_csv(TRADES_CSV)
    except pd.errors.EmptyDataError:
        return None
    return df.sort_values("timestamp").reset_index(drop=True) if not df.empty else None


def compute_metrics(df: pd.DataFrame) -> dict:
    latest = df.iloc[-1]
    closes = df[df["action"] == "CLOSE"]
    pnl_deltas = df["pnl"].diff().reindex(closes.index)
    tc = len(closes)
    wc = int((pnl_deltas > 0).sum())
    return {
        "balance": float(latest["balance"]),
        "total_pnl": float(latest["pnl"]),
        "trade_count": tc,
        "win_rate": round(wc / tc * 100, 1) if tc else 0.0,
    }


def parse_explanation(explanation: str) -> tuple[str, list[dict]]:
    lines = str(explanation).split("\n")
    headline = lines[0].strip()
    rows = []
    for line in lines[1:]:
        m = EXPLANATION_LINE_RE.match(line)
        if m:
            rows.append({
                "skill": m.group("skill"),
                "weight": int(m.group("weight")),
                "score": int(m.group("score")),
                "summary": m.group("summary").strip(),
            })
    return headline, rows


def dominant_skill(headline: str) -> str:
    m = DOMINANT_RE.search(headline)
    return m.group(1).upper() if m else "—"


def load_open_positions() -> dict | None:
    if not PORTFOLIO_JSON.exists():
        return None
    try:
        with open(PORTFOLIO_JSON, encoding="utf-8") as f:
            return json.load(f).get("open_positions", {})
    except Exception:
        return None


def next_cycle_countdown(df: pd.DataFrame) -> str:
    try:
        last_ts = pd.to_datetime(df["timestamp"].max())
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC")
        remaining = (last_ts + pd.Timedelta(seconds=CYCLE_SECONDS) -
                     datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return "NOW"
        m, s = int(remaining // 60), int(remaining % 60)
        return f"{m:02d}:{s:02d}"
    except Exception:
        return "—"


@st.cache_data(ttl=60)
def load_live_prices() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sym in SYMBOLS:
        try:
            url = (f"https://api.bitget.com/api/v2/mix/market/ticker"
                   f"?symbol={sym}&productType=USDT-FUTURES")
            data = requests.get(url, timeout=6).json().get("data", [])
            if data:
                row = data[0]
                out[sym] = {
                    "price": float(row.get("lastPr", 0)),
                    "change": float(row.get("change24h", 0)),
                }
        except Exception:
            out[sym] = {"price": 0.0, "change": 0.0}
    return out


@st.cache_data(ttl=300)
def load_live_news() -> list[str]:
    headlines: list[str] = []
    for url in NEWS_FEEDS:
        try:
            r = requests.get(url, headers={"User-Agent": "SentinelCouncil/1.0"}, timeout=8)
            raw = re.findall(
                r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", r.text, re.DOTALL
            )
            headlines.extend(t.strip() for t in raw[1:6] if t.strip())
        except Exception:
            pass
    return headlines[:16]


# ─── Render Helpers ───────────────────────────────────────────────

def score_color(score: int) -> str:
    if score >= 60:
        return "#00ff87"
    if score < 40:
        return "#ff4d4d"
    return "#f5a623"


def signal_cls(decision: str) -> str:
    return {"BUY": "sig-buy", "SELL": "sig-sell"}.get(decision, "sig-hold")


def decision_cls(decision: str) -> str:
    return {"BUY": "d-buy", "SELL": "d-sell"}.get(decision, "d-hold")


def gauge_svg(confidence: float, decision: str) -> str:
    r, cx, cy = 62, 80, 82
    color = {"BUY": "#00ff87", "SELL": "#ff4d4d"}.get(decision, "#f5a623")
    bg_path = f"M {cx-r} {cy} A {r} {r} 0 0 0 {cx+r} {cy}"

    if confidence <= 0:
        fill_el = ""
    elif confidence >= 100:
        mid_x, mid_y = cx, cy - r
        fill_el = (
            f'<path d="M {cx-r} {cy} A {r} {r} 0 0 0 {mid_x} {mid_y} '
            f'A {r} {r} 0 0 0 {cx+r-0.01} {cy}" '
            f'fill="none" stroke="{color}" stroke-width="7" stroke-linecap="round" '
            f'style="filter:drop-shadow(0 0 5px {color})"/>'
        )
    else:
        angle_rad = math.radians((1 - confidence / 100) * 180)
        x_end = cx + r * math.cos(angle_rad)
        y_end = cy - r * math.sin(angle_rad)
        fill_path = f"M {cx-r} {cy} A {r} {r} 0 0 0 {x_end:.2f} {y_end:.2f}"
        fill_el = (
            f'<path d="{fill_path}" fill="none" stroke="{color}" stroke-width="7" '
            f'stroke-linecap="round" style="filter:drop-shadow(0 0 5px {color})"/>'
        )

    return f"""
<svg viewBox="0 0 160 100" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:150px">
    <path d="{bg_path}" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="7" stroke-linecap="round"/>
    {fill_el}
    <text x="{cx}" y="{cy+4}" text-anchor="middle" fill="{color}"
          font-family="Share Tech Mono,monospace" font-size="26" font-weight="bold">{confidence:.0f}</text>
    <text x="{cx}" y="{cy+18}" text-anchor="middle" fill="#2a3d52"
          font-family="Share Tech Mono,monospace" font-size="7.5" letter-spacing="2">CONFIDENCE</text>
</svg>"""


def render_score_bars(rows: list[dict]) -> str:
    parts = []
    for r in rows:
        c = score_color(r["score"])
        parts.append(f"""
        <div class="score-row">
            <span class="score-skill">{r['skill'].upper()}</span>
            <div class="score-track">
                <div class="score-fill" style="width:{r['score']}%;background:{c};box-shadow:0 0 5px {c}55;"></div>
            </div>
            <span class="score-num" style="color:{c}">{r['score']}</span>
        </div>""")
    return f'<div class="score-bars">{"".join(parts)}</div>'


def render_signal_card(symbol: str, row: pd.Series) -> str:
    decision = str(row.get("decision", "HOLD"))
    confidence = float(row.get("confidence", 0))
    headline, skill_rows = parse_explanation(str(row.get("explanation", "")))
    dom = dominant_skill(headline)
    conf_color = score_color(int(confidence))
    ts = str(row.get("timestamp", ""))[:16].replace("T", " ")
    bars = render_score_bars(skill_rows)
    gauge = gauge_svg(confidence, decision)
    return f"""
    <div class="signal-card">
        <div class="signal-sym">◈ {symbol} &nbsp;<em>{ts}</em></div>
        <div style="display:flex;align-items:center;gap:1.2rem">
            <div>
                <div class="{signal_cls(decision)}">{decision}</div>
                <div class="sig-conf" style="color:{conf_color}">{confidence:.1f}%</div>
                <div class="sig-dom">▲ {dom}</div>
            </div>
            <div class="gauge-wrap" style="flex:1">{gauge}</div>
        </div>
        {bars}
    </div>"""


def render_breakdown_bars(rows: list[dict]) -> str:
    parts = []
    for r in rows:
        c = score_color(r["score"])
        parts.append(f"""
        <div class="bd-item">
            <div class="bd-header">
                <span class="bd-skill">{r['skill'].upper()}</span>
                <span class="bd-score" style="color:{c}">{r['score']}</span>
            </div>
            <div class="bd-track">
                <div class="bd-fill" style="width:{r['score']}%;background:{c};box-shadow:0 0 8px {c}55;"></div>
            </div>
            <div class="bd-weight">{r['weight']}% weight</div>
        </div>""")
    return "".join(parts)


def render_summary_rows(rows: list[dict]) -> str:
    parts = []
    for r in rows:
        c = score_color(r["score"])
        safe = html.escape(r["summary"])
        parts.append(f"""
        <div class="summary-row" style="border-left-color:{c}40">
            <div class="summary-tag" style="color:{c}">{r['skill'].upper()} &middot; {r['weight']}% &middot; SCORE {r['score']}</div>
            <div class="summary-text">{safe}</div>
        </div>""")
    return "".join(parts)


# ─── Page Setup ───────────────────────────────────────────────────

st.set_page_config(
    page_title="Sentinel Council",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(CSS, unsafe_allow_html=True)

# ── Masthead ──────────────────────────────────────────────────────

df = load_trades()
now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M UTC")
countdown = next_cycle_countdown(df) if df is not None else "—"

st.markdown(f"""
<div class="sc-masthead">
    <div>
        <div class="sc-wordmark">SENTINEL <em>COUNCIL</em></div>
        <div class="sc-tagline">AUTONOMOUS TRADING AGENT &nbsp;·&nbsp; FIVE-COUNCIL ARCHITECTURE &nbsp;·&nbsp; POWERED BY BITGET AGENT HUB</div>
    </div>
    <div class="sc-live-block">
        <div class="sc-live"><span class="pulse-dot"></span> LIVE</div>
        <div class="sc-clock">{now_utc}</div>
        <div class="sc-countdown">NEXT CYCLE &nbsp;{countdown}</div>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Live Price Strip ───────────────────────────────────────────────

prices = load_live_prices()
price_cards = ""
for sym in SYMBOLS:
    p = prices.get(sym, {"price": 0.0, "change": 0.0})
    chg = p["change"] * 100
    chg_color = "#00ff87" if chg >= 0 else "#ff4d4d"
    chg_sign = "▲" if chg >= 0 else "▼"
    label = SYM_LABELS.get(sym, sym)
    price_fmt = f"${p['price']:,.2f}" if p["price"] else "—"
    price_cards += f"""
    <div class="price-card">
        <div>
            <div class="price-sym">{label} / USDT</div>
            <div class="price-val">{price_fmt}</div>
        </div>
        <div class="price-chg" style="color:{chg_color}">{chg_sign} {abs(chg):.2f}%<br>
            <span style="font-size:.6rem;color:#2a3d52">24H</span>
        </div>
    </div>"""

st.markdown(f'<div class="price-strip">{price_cards}</div>', unsafe_allow_html=True)

if df is None:
    st.markdown("""
    <div class="sc-empty">
        AWAITING FIRST CYCLE<br>
        <span style="color:#2a3d52;font-size:.6rem">trades.csv is missing or empty</span><br><br>
        <code>python sentinel.py --once</code>
    </div>
    """, unsafe_allow_html=True)
else:
    # ── Portfolio Metrics ──────────────────────────────────────────
    st.markdown('<div class="sc-section">PORTFOLIO STATUS</div>', unsafe_allow_html=True)

    m = compute_metrics(df)
    pnl_cls = "pos" if m["total_pnl"] >= 0 else "neg"
    pnl_sign = "+" if m["total_pnl"] >= 0 else ""
    bal_cls = "pos" if m["balance"] >= STARTING_BALANCE else "neg"

    st.markdown(f"""
    <div class="metric-row">
        <div class="metric-card">
            <div class="metric-label">BALANCE (USDT)</div>
            <div class="metric-value {bal_cls}">${m['balance']:,.2f}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">TOTAL P&amp;L</div>
            <div class="metric-value {pnl_cls}">{pnl_sign}${m['total_pnl']:,.4f}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">CLOSED TRADES</div>
            <div class="metric-value">{m['trade_count']}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">WIN RATE</div>
            <div class="metric-value">{m['win_rate']:.1f}%</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    open_positions = load_open_positions()
    if open_positions:
        pos_rows = ""
        for sym, p in open_positions.items():
            pos_rows += f"""
            <tr>
                <td>{html.escape(p.get('symbol', sym))}</td>
                <td>${float(p.get('size', 0)):.2f}</td>
                <td>${float(p.get('entry_price', 0)):.4f}</td>
                <td class="ts-dim">{html.escape(str(p.get('opened_at', ''))[:19].replace('T', ' '))}</td>
            </tr>"""
        st.markdown(f"""
        <div class="audit-wrap" style="margin-top:.75rem">
        <table class="audit-table">
            <thead><tr><th>SYMBOL</th><th>SIZE</th><th>ENTRY PRICE</th><th>OPENED AT</th></tr></thead>
            <tbody>{pos_rows}</tbody>
        </table></div>
        """, unsafe_allow_html=True)

    # ── Equity Curve ───────────────────────────────────────────────
    st.markdown('<div class="sc-section">EQUITY CURVE</div>', unsafe_allow_html=True)

    eq_df = df[["timestamp", "balance"]].copy()
    eq_df["timestamp"] = pd.to_datetime(eq_df["timestamp"])
    eq_df = eq_df.drop_duplicates("timestamp").sort_values("timestamp")

    area_chart = (
        alt.Chart(eq_df)
        .mark_area(
            line={"color": "#00ff87", "strokeWidth": 1.5},
            color=alt.Gradient(
                gradient="linear",
                stops=[
                    alt.GradientStop(color="rgba(0,255,135,0.18)", offset=0),
                    alt.GradientStop(color="rgba(0,255,135,0)", offset=1),
                ],
                x1=1, x2=1, y1=1, y2=0,
            ),
        )
        .encode(
            x=alt.X("timestamp:T", title=None, axis=alt.Axis(
                labelColor="#2a3d52", gridColor="#0c1620", tickColor="#0c1620",
                labelFont="Share Tech Mono", labelFontSize=9, domainColor="#0c1620",
            )),
            y=alt.Y("balance:Q", title="BALANCE (USDT)", scale=alt.Scale(zero=False),
                axis=alt.Axis(
                    labelColor="#2a3d52", gridColor="#0c1620", tickColor="#0c1620",
                    labelFont="Share Tech Mono", labelFontSize=9, domainColor="#0c1620",
                    titleColor="#2a3d52", titleFont="Share Tech Mono", titleFontSize=8,
                )),
            tooltip=[
                alt.Tooltip("timestamp:T", title="Time"),
                alt.Tooltip("balance:Q", title="Balance", format="$,.2f"),
            ],
        )
    )
    baseline_rule = (
        alt.Chart(pd.DataFrame({"y": [STARTING_BALANCE]}))
        .mark_rule(strokeDash=[4, 3], color="#2a3d52", opacity=0.5, strokeWidth=1)
        .encode(y="y:Q")
    )
    equity_chart = (
        (area_chart + baseline_rule)
        .properties(height=160, background="#070b0f",
                    padding={"top": 12, "bottom": 12, "left": 12, "right": 12})
        .configure_view(strokeWidth=0, fill="#070b0f")
    )
    st.altair_chart(equity_chart, width="stretch")

    # ── Live News ──────────────────────────────────────────────────
    st.markdown('<div class="sc-section">LIVE CRYPTO NEWS</div>', unsafe_allow_html=True)

    news_items = load_live_news()
    if news_items:
        left_col, right_col = st.columns(2)
        for i, headline in enumerate(news_items):
            col = left_col if i % 2 == 0 else right_col
            with col:
                st.markdown(f"""
                <div class="news-card">
                    <div class="news-text">{html.escape(headline)}</div>
                </div>""", unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="font-family:'Share Tech Mono',monospace;font-size:.65rem;color:#2a3d52;padding:.5rem 0">
        NEWS FEEDS TEMPORARILY UNAVAILABLE</div>""", unsafe_allow_html=True)

    # ── Current Signals ────────────────────────────────────────────
    st.markdown('<div class="sc-section">CURRENT SIGNALS</div>', unsafe_allow_html=True)

    cards_html = ""
    for symbol in SYMBOLS:
        sym_df = df[df["symbol"] == symbol].sort_values("timestamp")
        if sym_df.empty:
            cards_html += f"""
            <div class="signal-card">
                <div class="signal-sym">◈ {symbol}</div>
                <div class="sig-hold" style="font-size:1.1rem">AWAITING</div>
            </div>"""
        else:
            cards_html += render_signal_card(symbol, sym_df.iloc[-1])

    st.markdown(f'<div class="signal-grid">{cards_html}</div>', unsafe_allow_html=True)

    # ── Decision Timeline ──────────────────────────────────────────
    st.markdown('<div class="sc-section">DECISION TIMELINE</div>', unsafe_allow_html=True)

    timeline_df = df[["timestamp", "symbol", "decision", "confidence"]].copy()
    timeline_df["timestamp"] = pd.to_datetime(timeline_df["timestamp"])
    COLOR_SCALE = alt.Scale(
        domain=["BUY", "SELL", "HOLD"],
        range=["#00ff87", "#ff4d4d", "#2a3d52"],
    )
    timeline = (
        alt.Chart(timeline_df)
        .mark_circle(opacity=0.85, size=70)
        .encode(
            x=alt.X("timestamp:T", title=None, axis=alt.Axis(
                labelColor="#2a3d52", gridColor="#0c1620", tickColor="#0c1620",
                labelFont="Share Tech Mono", labelFontSize=9, domainColor="#0c1620",
            )),
            y=alt.Y("symbol:N", title=None, axis=alt.Axis(
                labelColor="#5e7a94", tickColor="#0c1620", domainColor="#0c1620",
                labelFont="Share Tech Mono", labelFontSize=10,
            )),
            color=alt.Color("decision:N", scale=COLOR_SCALE,
                legend=alt.Legend(
                    labelColor="#5e7a94", titleColor="#2a3d52",
                    labelFont="Share Tech Mono", labelFontSize=10,
                    title="DECISION", orient="top-right",
                )),
            tooltip=[
                alt.Tooltip("timestamp:T", title="Time"),
                alt.Tooltip("symbol:N", title="Symbol"),
                alt.Tooltip("decision:N", title="Decision"),
                alt.Tooltip("confidence:Q", title="Confidence", format=".1f"),
            ],
        )
        .properties(height=130, background="#070b0f",
                    padding={"top": 12, "bottom": 12, "left": 12, "right": 12})
        .configure_view(strokeWidth=0, fill="#070b0f")
    )
    st.altair_chart(timeline, width="stretch")

    # ── Skill Breakdown ────────────────────────────────────────────
    st.markdown('<div class="sc-section">SKILL BREAKDOWN</div>', unsafe_allow_html=True)

    syms_with_data = sorted(df["symbol"].unique())
    tabs = st.tabs([f"◈ {s}" for s in syms_with_data])

    for symbol, tab in zip(syms_with_data, tabs):
        with tab:
            sym_df = df[df["symbol"] == symbol].sort_values("timestamp")
            latest = sym_df.iloc[-1]
            headline, skill_rows = parse_explanation(str(latest.get("explanation", "")))
            decision = str(latest.get("decision", "HOLD"))
            confidence = float(latest.get("confidence", 0))
            conf_color = score_color(int(confidence))

            st.markdown(f"""
            <div style="font-family:'Share Tech Mono',monospace;font-size:.68rem;
                        color:#2a3d52;letter-spacing:.1em;margin-bottom:1.1rem">
                {decision} @ <span style="color:{conf_color}">{confidence:.1f}%</span>
                &nbsp;·&nbsp; {html.escape(headline[:120])}
            </div>""", unsafe_allow_html=True)

            if skill_rows:
                left, right = st.columns([1, 2])
                with left:
                    st.markdown(render_breakdown_bars(skill_rows), unsafe_allow_html=True)
                with right:
                    st.markdown(render_summary_rows(skill_rows), unsafe_allow_html=True)

    # ── Confidence Trend ───────────────────────────────────────────
    st.markdown('<div class="sc-section">CONFIDENCE TREND</div>', unsafe_allow_html=True)

    trend_df = df[["timestamp", "symbol", "confidence"]].copy()
    trend_df["timestamp"] = pd.to_datetime(trend_df["timestamp"])
    SYM_COLORS = {"BTCUSDT": "#f5a623", "ETHUSDT": "#4d9eff", "SOLUSDT": "#c084fc"}

    lines = (
        alt.Chart(trend_df)
        .mark_line(point=alt.OverlayMarkDef(filled=True, size=35), strokeWidth=1.5)
        .encode(
            x=alt.X("timestamp:T", title=None, axis=alt.Axis(
                labelColor="#2a3d52", gridColor="#0c1620", tickColor="#0c1620",
                labelFont="Share Tech Mono", labelFontSize=9, domainColor="#0c1620",
            )),
            y=alt.Y("confidence:Q", title="CONFIDENCE %", scale=alt.Scale(domain=[0, 100]),
                axis=alt.Axis(
                    labelColor="#2a3d52", gridColor="#0c1620", tickColor="#0c1620",
                    labelFont="Share Tech Mono", labelFontSize=9, domainColor="#0c1620",
                    titleColor="#2a3d52", titleFont="Share Tech Mono", titleFontSize=8,
                )),
            color=alt.Color("symbol:N",
                scale=alt.Scale(domain=list(SYM_COLORS.keys()), range=list(SYM_COLORS.values())),
                legend=alt.Legend(
                    labelColor="#5e7a94", titleColor="#2a3d52",
                    labelFont="Share Tech Mono", labelFontSize=10,
                    title="SYMBOL", orient="top-right",
                )),
            tooltip=[
                alt.Tooltip("timestamp:T", title="Time"),
                alt.Tooltip("symbol:N", title="Symbol"),
                alt.Tooltip("confidence:Q", title="Confidence", format=".1f"),
            ],
        )
    )
    buy_rule = (
        alt.Chart(pd.DataFrame({"y": [BUY_THRESHOLD]}))
        .mark_rule(strokeDash=[5, 4], color="#00ff87", opacity=0.35, strokeWidth=1)
        .encode(y="y:Q")
    )
    buy_label = (
        alt.Chart(pd.DataFrame({"y": [BUY_THRESHOLD]}))
        .mark_text(text="BUY", align="right", baseline="bottom", dx=-6, dy=-2,
                   color="#00ff87", opacity=0.5, fontSize=9, font="Share Tech Mono")
        .encode(y="y:Q")
    )
    sell_rule = (
        alt.Chart(pd.DataFrame({"y": [SELL_THRESHOLD]}))
        .mark_rule(strokeDash=[5, 4], color="#ff4d4d", opacity=0.35, strokeWidth=1)
        .encode(y="y:Q")
    )
    trend_chart = (
        (lines + buy_rule + buy_label + sell_rule)
        .properties(height=260, background="#070b0f",
                    padding={"top": 16, "bottom": 16, "left": 16, "right": 16})
        .configure_view(strokeWidth=0, fill="#070b0f")
    )
    st.altair_chart(trend_chart, width="stretch")

    # ── Decision Log ───────────────────────────────────────────────
    st.markdown('<div class="sc-section">DECISION LOG</div>', unsafe_allow_html=True)

    timestamps = pd.to_datetime(df["timestamp"])
    hours_span = round((timestamps.max() - timestamps.min()).total_seconds() / 3600, 1)
    buy_c = int((df["decision"] == "BUY").sum())
    sell_c = int((df["decision"] == "SELL").sum())
    hold_c = int((df["decision"] == "HOLD").sum())

    st.markdown(f"""
    <div style="font-family:'Share Tech Mono',monospace;font-size:.62rem;color:#2a3d52;
                letter-spacing:.12em;margin-bottom:.75rem">
        {len(df)} DECISIONS &nbsp;·&nbsp; {hours_span}H SPAN &nbsp;·&nbsp;
        <span style="color:#00ff87">{buy_c} BUY</span> &nbsp;
        <span style="color:#ff4d4d">{sell_c} SELL</span> &nbsp;
        <span style="color:#2a3d52">{hold_c} HOLD</span>
    </div>""", unsafe_allow_html=True)

    recent = df.sort_values("timestamp", ascending=False).head(25)
    rows_html = ""
    for _, row in recent.iterrows():
        dec = str(row.get("decision", "HOLD"))
        ts = str(row.get("timestamp", ""))[:19].replace("T", " ")
        pnl_v = float(row.get("pnl", 0))
        rows_html += f"""
        <tr>
            <td class="ts-dim">{html.escape(ts)}</td>
            <td>{html.escape(str(row.get('symbol', '')))}</td>
            <td class="{decision_cls(dec)}">{dec}</td>
            <td style="color:#5e7a94">{float(row.get('confidence', 0)):.1f}%</td>
            <td style="color:#2a3d52">{html.escape(str(row.get('action', '')))}</td>
            <td style="color:#5e7a94">${float(row.get('balance', 0)):,.2f}</td>
            <td style="color:{'#00ff87' if pnl_v >= 0 else '#ff4d4d'}">{pnl_v:+.4f}</td>
        </tr>"""

    st.markdown(f"""
    <div class="audit-wrap">
    <table class="audit-table">
        <thead><tr>
            <th>TIMESTAMP</th><th>SYMBOL</th><th>DECISION</th>
            <th>CONFIDENCE</th><th>ACTION</th><th>BALANCE</th><th>P&amp;L</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
    </table></div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.download_button(
        "↓  EXPORT AUDIT CSV",
        data=TRADES_CSV.read_bytes(),
        file_name="sentinel_council_audit.csv",
        mime="text/csv",
    )

# ── Footer ─────────────────────────────────────────────────────────
st.markdown(f"""
<div class="sc-footer">
    <span>SENTINEL COUNCIL &nbsp;·&nbsp; POWERED BY BITGET AGENT HUB</span>
    <span>AUTO-REFRESH ↺ {REFRESH_SECONDS}s</span>
</div>""", unsafe_allow_html=True)

# ── TV News Ticker (fixed bottom) ─────────────────────────────────
ticker_news = load_live_news()
if ticker_news:
    ticker_text = "  &nbsp;&nbsp;&nbsp;◈&nbsp;&nbsp;&nbsp; ".join(
        f"<em>{html.escape(h)}</em>" for h in ticker_news
    )
    # Double for seamless loop
    ticker_double = f"{ticker_text}  &nbsp;&nbsp;&nbsp;◈&nbsp;&nbsp;&nbsp;  {ticker_text}"
    st.markdown(f"""
    <div class="ticker-wrap">
        <div class="ticker-badge">◈ CRYPTO NEWS</div>
        <div class="ticker-track">
            <span class="ticker-inner">{ticker_double}</span>
        </div>
    </div>""", unsafe_allow_html=True)

time.sleep(REFRESH_SECONDS)
st.rerun()
