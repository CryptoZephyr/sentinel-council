"""
Sentinel Council — Trading Agent Dashboard
Five analysts. One consensus. Zero emotion.
Run with: python3.12 -m streamlit run dashboard.py
"""

import html
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

TRADES_CSV = Path("trades.csv")
PORTFOLIO_JSON = Path("data/portfolio.json")
CYCLE_STATUS_JSON = Path("data/cycle_status.json")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BGBUSDT", "AVAXUSDT", "DOGEUSDT"]
SYM_LABELS = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "BGBUSDT": "BGB", "AVAXUSDT": "AVAX", "DOGEUSDT": "DOGE"}
BUY_THRESHOLD = 72.0
SELL_THRESHOLD = 28.0
WATCH_BUY_THRESHOLD = 60.0
WATCH_SELL_THRESHOLD = 40.0
STARTING_BALANCE = 10_000.0
CYCLE_SECONDS = 3600
REFRESH_SECONDS = 10
SL_PCT = -0.02
TP_PCT = 0.05
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

# ─── ANALYSTS ────────────────────────────────────────────────────
_ANALYSTS = ["macro", "technical", "sentiment", "news", "intel"]
_A_LABEL = {"macro": "MACRO", "technical": "TECH", "sentiment": "SENT", "news": "NEWS", "intel": "INTEL"}
_A_WEIGHT = {"macro": "30%", "technical": "30%", "sentiment": "20%", "news": "10%", "intel": "10%"}
_A_WEIGHT_NUM = {"macro": 30, "technical": 30, "sentiment": 20, "news": 10, "intel": 10}
_A_ICON = {"macro": "◎", "technical": "▲", "sentiment": "◆", "news": "●", "intel": "◈"}

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
.sc-wordmark { font-family:'Chakra Petch',monospace !important;font-size:2.4rem;font-weight:700;letter-spacing:0.14em;color:#dce8f5;line-height:1;margin:0; }
.sc-wordmark em { color:#00ff87;font-style:normal; }
.sc-tagline { font-family:'Share Tech Mono',monospace !important;font-size:0.62rem;color:#5e7a94;letter-spacing:0.22em;margin-top:0.45rem; }
.sc-live-block { text-align:right; }
.sc-live { font-family:'Share Tech Mono',monospace !important;font-size:0.72rem;color:#00ff87;letter-spacing:0.18em;display:inline-flex;align-items:center;gap:0.45rem; }
.pulse-dot { width:7px;height:7px;background:#00ff87;border-radius:50%;display:inline-block;box-shadow:0 0 7px #00ff87;animation:pulse-anim 1.6s ease-in-out infinite; }
@keyframes pulse-anim { 0%,100%{opacity:1;box-shadow:0 0 7px #00ff87} 50%{opacity:.3;box-shadow:0 0 2px #00ff87} }
.sc-clock { font-family:'Share Tech Mono',monospace !important;font-size:0.6rem;color:#2a3d52;letter-spacing:0.12em;margin-top:0.3rem; }
.sc-countdown { font-family:'Share Tech Mono',monospace !important;font-size:0.62rem;color:#f5a623;letter-spacing:0.12em;margin-top:0.2rem; }

/* ── How It Works ── */
.hiw-section { background:#0a1218;border:1px solid rgba(0,255,135,0.1);border-radius:4px;padding:1.4rem 2rem;margin-bottom:1.5rem;position:relative;overflow:hidden; }
.hiw-section::before { content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(0,255,135,.35),transparent); }
.hiw-title { font-family:'Share Tech Mono',monospace !important;font-size:0.58rem;color:#00ff87;letter-spacing:0.3em;margin-bottom:1rem;text-align:center; }
.hiw-flow { display:flex;align-items:center;justify-content:center;gap:0.6rem;flex-wrap:nowrap; }
.hiw-analysts { display:flex;flex-direction:column;gap:0.25rem;flex-shrink:0; }
.hiw-pill { font-family:'Share Tech Mono',monospace !important;font-size:0.56rem;color:#5e7a94;letter-spacing:0.08em;padding:0.18rem 0.6rem;border:1px solid rgba(0,255,135,0.12);border-radius:2px;display:flex;justify-content:space-between;gap:0.6rem;white-space:nowrap; }
.hiw-pill-weight { color:#2a3d52; }
.hiw-arrow { color:rgba(0,255,135,0.4);font-family:'Share Tech Mono',monospace;font-size:1.4rem;flex-shrink:0;line-height:1; }
.hiw-box { border:1px solid rgba(0,255,135,0.2);border-radius:3px;padding:0.7rem 1rem;text-align:center;flex-shrink:0;min-width:90px; }
.hiw-box-main { border-color:rgba(0,255,135,0.35);background:rgba(0,255,135,0.03); }
.hiw-box-title { font-family:'Chakra Petch',monospace !important;font-size:0.78rem;color:#00ff87;letter-spacing:0.15em;font-weight:600; }
.hiw-box-sub { font-family:'Share Tech Mono',monospace !important;font-size:0.5rem;color:#2a3d52;letter-spacing:0.1em;margin-top:0.15rem; }
.hiw-rules { text-align:center;font-family:'Share Tech Mono',monospace !important;font-size:0.6rem;color:#2a3d52;letter-spacing:0.1em;margin-top:1rem;padding-top:0.75rem;border-top:1px solid rgba(255,255,255,0.03); }
.hiw-rules em { font-style:normal; }
.hiw-rules .r-buy { color:#00ff87; }
.hiw-rules .r-sell { color:#ff4d4d; }
.hiw-rules .r-watch { color:#f5a623; }
.hiw-rules .r-wait { color:#2a3d52; }

/* ── Price Strip ── */
.price-strip { display:grid;grid-template-columns:repeat(6,1fr);gap:0.6rem;margin-bottom:1.5rem; }
.price-card { background:#0a1218;border:1px solid rgba(0,255,135,0.08);border-radius:3px;padding:0.65rem 0.9rem;display:flex;justify-content:space-between;align-items:center;transition:border-color .2s; }
.price-card:hover { border-color:rgba(0,255,135,0.2); }
.price-sym { font-family:'Chakra Petch',monospace !important;font-size:0.68rem;color:#2a3d52;letter-spacing:0.12em; }
.price-val { font-family:'Share Tech Mono',monospace !important;font-size:0.95rem;color:#c8d8ea;line-height:1;margin-top:0.1rem; }
.price-chg { font-family:'Share Tech Mono',monospace !important;font-size:0.68rem;letter-spacing:0.05em;text-align:right; }

/* ── Section Labels ── */
.sc-section { font-family:'Share Tech Mono',monospace !important;font-size:0.62rem;letter-spacing:0.28em;color:#2a3d52;text-transform:uppercase;border-bottom:1px solid #0c1620;padding-bottom:0.4rem;margin:2rem 0 1rem; }

/* ── Metric Cards ── */
.metric-row { display:grid;grid-template-columns:repeat(4,1fr);gap:0.9rem;margin-bottom:0.5rem; }
.metric-card { background:#0d1520;border:1px solid rgba(0,255,135,0.1);border-radius:3px;padding:1rem 1.2rem;position:relative;overflow:hidden;transition:border-color .25s; }
.metric-card:hover { border-color:rgba(0,255,135,0.25); }
.metric-card::after { content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(0,255,135,.45),transparent); }
.metric-label { font-family:'Share Tech Mono',monospace !important;font-size:0.58rem;color:#2a3d52;letter-spacing:0.18em;margin-bottom:0.45rem; }
.metric-value { font-family:'Share Tech Mono',monospace !important;font-size:1.6rem;color:#c8d8ea;line-height:1;font-weight:400; }
.metric-value.pos { color:#00ff87; }
.metric-value.neg { color:#ff4d4d; }

/* ── Council Chamber ── */
.council-grid { display:grid;grid-template-columns:repeat(3,1fr);gap:0.9rem;margin-bottom:0.5rem; }
.council-card { background:#0d1520;border:1px solid rgba(0,255,135,0.1);border-radius:4px;padding:1rem 1.1rem 1.2rem;transition:border-color .25s;position:relative;overflow:hidden; }
.council-card:hover { border-color:rgba(0,255,135,0.25); }
.council-card::after { content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(0,255,135,.3),transparent); }
.council-sym { font-family:'Chakra Petch',monospace !important;font-size:0.72rem;color:#5e7a94;letter-spacing:0.12em;margin-bottom:0.3rem; }
.council-radar { display:flex;justify-content:center;margin:0.2rem 0; }
.council-verdict { display:flex;align-items:center;justify-content:space-between;margin-top:0.4rem;padding-top:0.5rem;border-top:1px solid rgba(255,255,255,0.03); }
.council-decision { font-family:'Chakra Petch',monospace !important;font-size:1.1rem;font-weight:700;letter-spacing:0.12em; }
.council-decision.d-buy { color:#00ff87;text-shadow:0 0 15px rgba(0,255,135,.35); }
.council-decision.d-sell { color:#ff4d4d;text-shadow:0 0 15px rgba(255,77,77,.35); }
.council-decision.d-watch { color:#f5a623;text-shadow:0 0 12px rgba(245,166,35,.25); }
.council-decision.d-wait { color:#2a3d52; }
.council-conf { font-family:'Share Tech Mono',monospace !important;font-size:0.85rem;font-weight:600; }
.council-proximity { font-family:'Share Tech Mono',monospace !important;font-size:0.56rem;letter-spacing:0.06em;margin-top:0.35rem;text-align:center; }
.council-insight { font-family:'DM Sans',sans-serif !important;font-size:0.68rem;color:#3d5166;line-height:1.35;margin-top:0.4rem;padding:0.45rem 0.6rem;background:rgba(0,0,0,0.2);border-radius:2px;border-left:2px solid rgba(255,255,255,0.04); }

/* ── Signal Intelligence Matrix ── */
.matrix-wrap { overflow-x:auto;margin-bottom:0.5rem; }
.matrix-table { width:100%;border-collapse:separate;border-spacing:3px; }
.matrix-th { font-family:'Share Tech Mono',monospace !important;font-size:0.58rem;color:#2a3d52;letter-spacing:0.12em;text-align:center;padding:0.4rem 0.5rem;white-space:nowrap; }
.matrix-th-left { text-align:left !important;padding-left:0 !important; }
.matrix-sym { font-family:'Chakra Petch',monospace !important;font-size:0.72rem;letter-spacing:0.1em;color:#5e7a94;padding:0.5rem 0.8rem 0.5rem 0;white-space:nowrap;vertical-align:middle; }
.matrix-cell { border-radius:2px;text-align:center;padding:0.5rem 0.3rem;font-family:'Share Tech Mono',monospace !important;font-size:0.82rem;font-weight:600;min-width:50px;vertical-align:middle; }
.matrix-conf { border-radius:2px;text-align:center;padding:0.5rem 0.6rem;font-family:'Share Tech Mono',monospace !important;font-size:0.82rem;font-weight:700;min-width:56px;vertical-align:middle; }
.matrix-dec { border-radius:2px;text-align:center;padding:0.5rem 0.6rem;font-family:'Chakra Petch',monospace !important;font-size:0.72rem;font-weight:700;letter-spacing:0.08em;min-width:62px;vertical-align:middle; }

/* ── Open Positions Monitor ── */
.pos-grid { display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:0.7rem;margin-bottom:0.5rem; }
.pos-card { background:#0a1218;border:1px solid rgba(0,255,135,0.1);border-radius:3px;padding:0.9rem 1.1rem;transition:border-color .2s; }
.pos-card:hover { border-color:rgba(0,255,135,0.22); }
.pos-sym { font-family:'Chakra Petch',monospace !important;font-size:0.72rem;color:#2a3d52;letter-spacing:0.15em;margin-bottom:0.65rem; }
.pos-row { display:flex;justify-content:space-between;align-items:baseline;margin-bottom:0.3rem; }
.pos-label { font-family:'Share Tech Mono',monospace !important;font-size:0.56rem;color:#2a3d52;letter-spacing:0.1em; }
.pos-val { font-family:'Share Tech Mono',monospace !important;font-size:0.7rem;color:#c8d8ea; }
.pos-pnl-pos { font-family:'Share Tech Mono',monospace !important;font-size:1.05rem;color:#00ff87;font-weight:600;text-shadow:0 0 10px rgba(0,255,135,.3); }
.pos-pnl-neg { font-family:'Share Tech Mono',monospace !important;font-size:1.05rem;color:#ff4d4d;font-weight:600;text-shadow:0 0 10px rgba(255,77,77,.3); }
.pos-divider { border:none;border-top:1px solid rgba(255,255,255,.04);margin:.45rem 0; }
.pos-none { font-family:'Share Tech Mono',monospace !important;font-size:.62rem;color:#1a2a38;letter-spacing:.18em;padding:1.2rem 0;text-align:center; }

/* ── Audit Table ── */
.audit-wrap { overflow-x:auto; }
.audit-table { width:100%;border-collapse:collapse; }
.audit-table th { font-family:'Share Tech Mono',monospace !important;font-size:.58rem;color:#2a3d52;letter-spacing:.18em;text-align:left;padding:.45rem .7rem;border-bottom:1px solid #0c1620;white-space:nowrap; }
.audit-table td { font-family:'Share Tech Mono',monospace !important;font-size:.7rem;color:#5e7a94;padding:.45rem .7rem;border-bottom:1px solid rgba(255,255,255,.025);white-space:nowrap; }
.audit-table tr:hover td { background:rgba(0,255,135,.018); }
.td-buy  { color:#00ff87 !important;font-weight:600; }
.td-sell { color:#ff4d4d !important;font-weight:600; }
.td-watch { color:#f5a623 !important;font-weight:600; }
.td-wait { color:#2a3d52 !important; }
.ts-dim { color:#2a3d52 !important;font-size:.65rem !important; }

/* ── Skill Breakdown ── */
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

/* ── Responsive ── */
@media (max-width: 1100px) {
    .council-grid { grid-template-columns:repeat(2,1fr); }
    .price-strip { grid-template-columns:repeat(3,1fr); }
    .metric-row { grid-template-columns:repeat(2,1fr); }
}
@media (max-width: 768px) {
    .council-grid { grid-template-columns:repeat(2,1fr); }
    .price-strip { grid-template-columns:repeat(3,1fr); }
    .metric-row { grid-template-columns:repeat(2,1fr); }
    .sc-masthead { flex-direction:column;align-items:flex-start;gap:.75rem; }
    .sc-live-block { text-align:left; }
    .sc-wordmark { font-size:1.8rem; }
    .hiw-flow { flex-wrap:wrap;gap:0.4rem; }
}
@media (max-width: 600px) {
    .block-container { padding-left:.65rem !important;padding-right:.65rem !important; }
    .council-grid { grid-template-columns:1fr; }
    .price-strip { grid-template-columns:repeat(2,1fr); }
    .metric-row { grid-template-columns:repeat(2,1fr); }
    .sc-wordmark { font-size:1.5rem; }
    .hiw-flow { flex-direction:column; }
    .hiw-arrow { transform:rotate(90deg); }
}

/* ── Cycle State Banner ── */
.cycle-banner { display:flex;align-items:center;justify-content:space-between;background:#0a1218;border:1px solid rgba(0,255,135,0.12);border-radius:4px;padding:1rem 1.5rem;margin-bottom:0.8rem;position:relative;overflow:hidden; }
.cycle-banner::before { content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(0,255,135,.35),transparent); }
.cycle-status { display:flex;align-items:center;gap:0.8rem; }
.cycle-status-badge { font-family:'Share Tech Mono',monospace !important;font-size:0.62rem;letter-spacing:0.18em;padding:0.3rem 0.8rem;border-radius:2px;font-weight:600; }
.badge-execution { background:rgba(0,255,135,0.15);color:#00ff87;border:1px solid rgba(0,255,135,0.3); }
.badge-watch { background:rgba(245,166,35,0.12);color:#f5a623;border:1px solid rgba(245,166,35,0.25); }
.badge-low { background:rgba(42,61,82,0.2);color:#3d5166;border:1px solid rgba(42,61,82,0.4); }
.cycle-status-desc { font-family:'DM Sans',sans-serif !important;font-size:0.72rem;color:#3d5166; }
.cycle-top-opp { text-align:right; }
.cycle-top-label { font-family:'Share Tech Mono',monospace !important;font-size:0.48rem;color:#2a3d52;letter-spacing:0.22em; }
.cycle-top-sym { font-family:'Chakra Petch',monospace !important;font-size:1.15rem;font-weight:700;letter-spacing:0.1em; }
.cycle-top-dist { font-family:'Share Tech Mono',monospace !important;font-size:0.58rem;margin-top:0.12rem; }

/* ── System Message Panel ── */
.sys-panel { background:#0a1218;border:1px solid rgba(0,255,135,0.06);border-radius:3px;padding:0.85rem 1.3rem;margin-bottom:1.5rem;display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem; }
.sys-item-label { font-family:'Share Tech Mono',monospace !important;font-size:0.48rem;color:#2a3d52;letter-spacing:0.2em;margin-bottom:0.25rem; }
.sys-item-text { font-family:'DM Sans',sans-serif !important;font-size:0.72rem;color:#5e7a94;line-height:1.4; }

/* ── Top Opportunity Highlight ── */
.council-card.top-opp { border-color:rgba(0,255,135,0.3) !important;background:linear-gradient(135deg,#0d1520,#0f1a24) !important;box-shadow:0 0 24px rgba(0,255,135,0.05),inset 0 0 40px rgba(0,255,135,0.015); }
.council-card.top-opp::after { background:linear-gradient(90deg,transparent,rgba(0,255,135,.55),transparent) !important; }
.top-opp-badge { font-family:'Share Tech Mono',monospace !important;font-size:0.48rem;color:#00ff87;letter-spacing:0.22em;text-align:center;margin-bottom:0.25rem;opacity:0.85; }

/* ── Rank Badge ── */
.rank-badge { font-family:'Chakra Petch',monospace !important;font-size:0.55rem;color:#2a3d52;letter-spacing:0.06em; }
.rank-num { font-size:0.65rem;color:#3d5166;font-weight:600; }

/* ── Direction Indicators ── */
.dir-toward-buy { color:rgba(0,255,135,0.55) !important; }
.dir-toward-sell { color:rgba(255,77,77,0.55) !important; }
.dir-neutral { color:#2a3d52 !important; }
.dir-line { font-family:'Share Tech Mono',monospace !important;font-size:0.5rem;letter-spacing:0.06em;text-align:center;margin-top:0.15rem; }

@media (max-width: 768px) {
    .cycle-banner { flex-direction:column;align-items:flex-start;gap:.6rem; }
    .cycle-top-opp { text-align:left; }
    .sys-panel { grid-template-columns:1fr; }
}
</style>
"""

# ─── Data Helpers ─────────────────────────────────────────────────


def load_trades() -> pd.DataFrame | None:
    if not TRADES_CSV.exists():
        return None
    try:
        df = pd.read_csv(TRADES_CSV)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError):
        return None
    except Exception:
        return None
    if df.empty or "timestamp" not in df.columns:
        return None
    if "trade_pnl" not in df.columns:
        df["trade_pnl"] = 0.0
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    if df.empty:
        return None
    return df.sort_values("timestamp").reset_index(drop=True) if not df.empty else None


def compute_metrics(df: pd.DataFrame) -> dict[str, Any]:
    latest = df.iloc[-1]
    closes = df[df.get("action", "") == "CLOSE"] if "action" in df.columns else pd.DataFrame()
    trade_pnl = pd.to_numeric(closes.get("trade_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    tc = int(len(closes))
    wc = int((trade_pnl > 0).sum())
    return {
        "balance": float(latest["balance"]),
        "total_pnl": float(latest["pnl"]),
        "trade_count": tc,
        "win_rate": round(wc / tc * 100, 1) if tc else 0.0,
    }


def parse_explanation(explanation: str) -> tuple[str, list[dict]]:
    lines = str(explanation).split("\n")
    headline = lines[0].strip()
    rows: list[dict] = []
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


def load_cycle_status() -> dict:
    if not CYCLE_STATUS_JSON.exists():
        return {}
    try:
        return json.loads(CYCLE_STATUS_JSON.read_text())
    except Exception:
        return {}


def next_cycle_countdown(df: pd.DataFrame | None) -> tuple[str, str, str]:
    """Returns (label, value, color) for the cycle timing display."""
    status = load_cycle_status()
    if status.get("status") == "running":
        return "CYCLE", "ANALYZING NOW", "#00ff87"
    try:
        ts_str = status.get("ts") or (df["timestamp"].max() if df is not None else None)
        if not ts_str:
            return "LAST CYCLE", "NO DATA", "#2a3d52"
        last_ts = pd.to_datetime(ts_str)
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC")
        elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
        remaining = CYCLE_SECONDS - elapsed
        if remaining > 0:
            m, s = int(remaining // 60), int(remaining % 60)
            return "NEXT CYCLE IN", f"{m:02d}:{s:02d}", "#f5a623"
        # Overdue — show how long ago
        ago = abs(elapsed)
        if ago < 120:
            return "LAST CYCLE", f"{int(ago)}S AGO", "#2a3d52"
        if ago < 7200:
            return "LAST CYCLE", f"{int(ago // 60)}M AGO", "#2a3d52"
        return "LAST CYCLE", f"{ago / 3600:.1f}H AGO", "#2a3d52"
    except Exception:
        return "LAST CYCLE", "UNKNOWN", "#2a3d52"


_CG_IDS = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",
    "BGBUSDT": "bitget-token",
    "AVAXUSDT": "avalanche-2",
    "DOGEUSDT": "dogecoin",
}


def load_latest_cycle(df: pd.DataFrame) -> list[dict]:
    """Extract the latest multi-symbol analysis cycle, ordered by backend rank."""
    required = {"rank", "distance_to_action", "direction", "top_opportunity"}
    if not required.issubset(df.columns):
        return []
    valid = df[df["rank"] > 0].sort_values("timestamp", ascending=False)
    if valid.empty:
        return []
    checked: set[float] = set()
    for _, row in valid.head(40).iterrows():
        ts = row["timestamp"]
        key = ts.timestamp()
        if any(abs(key - k) < 5 for k in checked):
            continue
        checked.add(key)
        batch = valid[abs((valid["timestamp"] - ts).dt.total_seconds()) < 30]
        if batch["symbol"].nunique() >= 3:
            cycle = batch.drop_duplicates("symbol", keep="first").sort_values("rank")
            return [_cycle_row(r) for _, r in cycle.iterrows()]
    return []


def _cycle_row(r: pd.Series) -> dict:
    sym = str(r["symbol"])
    return {
        "symbol": sym,
        "label": SYM_LABELS.get(sym, sym),
        "confidence": float(r.get("confidence", 0)),
        "decision": str(r.get("decision", "WAIT")),
        "action": str(r.get("action", "")),
        "distance": float(r.get("distance_to_action", 0)),
        "direction": str(r.get("direction", "neutral")),
        "rank": int(r.get("rank", 0)),
        "top_opportunity": str(r.get("top_opportunity", False)).strip().lower() == "true",
        "explanation": str(r.get("explanation", "")),
    }


def determine_system_status(cycle: list[dict]) -> tuple[str, str, str]:
    """Returns (status_label, badge_css_class, description)."""
    if not cycle:
        return "OFFLINE", "badge-low", "Awaiting first cycle"
    actions = {c["action"] for c in cycle}
    decisions = {c["decision"] for c in cycle}
    if actions & {"OPEN_LONG", "OPEN_SHORT"}:
        return "ACTIVE EXECUTION", "badge-execution", "Executing trades this cycle"
    if "WATCH" in decisions:
        return "ACTIVE WATCH", "badge-watch", "Assets approaching action thresholds"
    return "LOW CONVICTION", "badge-low", "No actionable signals detected"


@st.cache_data(ttl=300)
def load_live_prices() -> dict[str, dict]:
    out: dict[str, dict] = {s: {"price": 0.0, "change": 0.0} for s in SYMBOLS}
    for sym in SYMBOLS:
        try:
            url = (f"https://api.bitget.com/api/v2/mix/market/ticker"
                   f"?symbol={sym}&productType=USDT-FUTURES")
            data = requests.get(url, timeout=3).json().get("data", [])
            if data:
                row = data[0]
                price = float(row.get("lastPr", 0))
                if price > 0:
                    out[sym] = {"price": price, "change": float(row.get("change24h", 0))}
        except Exception:
            pass
    missing = [s for s in SYMBOLS if out[s]["price"] == 0.0 and s in _CG_IDS]
    if missing:
        try:
            ids = ",".join(_CG_IDS[s] for s in missing)
            url = (f"https://api.coingecko.com/api/v3/simple/price"
                   f"?ids={ids}&vs_currencies=usd&include_24hr_change=true")
            cg = requests.get(url, timeout=5).json()
            for sym in missing:
                entry = cg.get(_CG_IDS[sym], {})
                if entry:
                    out[sym] = {
                        "price": float(entry.get("usd", 0)),
                        "change": float(entry.get("usd_24h_change", 0)) / 100,
                    }
        except Exception:
            pass
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


# ─── SVG & Render Helpers ────────────────────────────────────────


def score_color(score: int) -> str:
    if score >= 60:
        return "#00ff87"
    if score < 40:
        return "#ff4d4d"
    return "#f5a623"


def decision_cls(decision: str) -> str:
    return {"BUY": "td-buy", "SELL": "td-sell", "WATCH": "td-watch"}.get(decision, "td-wait")


def decision_card_cls(decision: str) -> str:
    return {"BUY": "d-buy", "SELL": "d-sell", "WATCH": "d-watch"}.get(decision, "d-wait")


def decision_color(decision: str) -> str:
    return {"BUY": "#00ff87", "SELL": "#ff4d4d", "WATCH": "#f5a623"}.get(decision, "#2a3d52")


def threshold_proximity(confidence: float, decision: str) -> tuple[str, str]:
    """Returns (text, color) for threshold proximity indicator."""
    if decision == "BUY":
        gap = confidence - BUY_THRESHOLD
        return f"▲ {gap:.1f} above BUY threshold", "#00ff87"
    elif decision == "SELL":
        gap = SELL_THRESHOLD - confidence
        return f"▼ {gap:.1f} below SELL threshold", "#ff4d4d"
    else:
        to_buy = BUY_THRESHOLD - confidence
        to_sell = confidence - SELL_THRESHOLD
        if to_buy <= to_sell:
            return f"▲ {to_buy:.1f} to BUY", "rgba(0,255,135,0.5)"
        else:
            return f"▼ {to_sell:.1f} above SELL", "rgba(255,77,77,0.5)"


def council_radar_svg(scores: dict[str, int], confidence: float, decision: str) -> str:
    """Pentagon radar chart showing 5 analyst scores around a council table."""
    analysts = ["macro", "technical", "sentiment", "news", "intel"]
    labels = ["MACRO", "TECH", "SENT", "NEWS", "INTEL"]
    weights = [30, 30, 20, 10, 10]

    vb = 220
    cx, cy = vb / 2, vb / 2 + 4
    r_max = 72

    def vtx(angle_deg: float, radius: float) -> tuple[float, float]:
        rad = math.radians(angle_deg)
        return (cx + radius * math.cos(rad), cy + radius * math.sin(rad))

    angles = [-90 + i * 72 for i in range(5)]

    # Grid pentagons at 25%, 50%, 75%, 100%
    grid = ""
    for pct in [25, 50, 75, 100]:
        r = r_max * pct / 100
        pts = " ".join(f"{vtx(a, r)[0]:.1f},{vtx(a, r)[1]:.1f}" for a in angles)
        opacity = "0.08" if pct == 50 else "0.04"
        grid += f'<polygon points="{pts}" fill="none" stroke="rgba(255,255,255,{opacity})" stroke-width="0.5"/>\n'

    # Axis lines
    axes = ""
    for a in angles:
        x, y = vtx(a, r_max)
        axes += f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="rgba(255,255,255,0.04)" stroke-width="0.5"/>\n'

    # Data polygon
    color = decision_color(decision)
    if color == "#2a3d52":
        color = "#f5a623"
    data_pts = []
    for i, a in enumerate(angles):
        s = scores.get(analysts[i], 50)
        r = max(r_max * s / 100, r_max * 0.05)
        data_pts.append(vtx(a, r))

    pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in data_pts)
    polygon = (
        f'<polygon points="{pts_str}" fill="{color}" fill-opacity="0.12" '
        f'stroke="{color}" stroke-width="1.5" stroke-linejoin="round" '
        f'style="filter:drop-shadow(0 0 6px {color}40)"/>\n'
    )

    # Data dots
    dots = ""
    for i, (x, y) in enumerate(data_pts):
        s = scores.get(analysts[i], 50)
        dc = score_color(s)
        dots += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{dc}" style="filter:drop-shadow(0 0 3px {dc})"/>\n'

    # Labels
    label_els = ""
    for i, a in enumerate(angles):
        lx, ly = vtx(a, r_max + 22)
        s = scores.get(analysts[i], 50)
        sc = score_color(s)
        w = weights[i]
        label_els += (
            f'<text x="{lx:.1f}" y="{ly - 5:.1f}" text-anchor="middle" fill="#2a3d52" '
            f'font-family="Share Tech Mono,monospace" font-size="6.5" letter-spacing="1.5">'
            f'{labels[i]} {w}%</text>\n'
        )
        label_els += (
            f'<text x="{lx:.1f}" y="{ly + 7:.1f}" text-anchor="middle" fill="{sc}" '
            f'font-family="Share Tech Mono,monospace" font-size="12" font-weight="700">'
            f'{s}</text>\n'
        )

    # Threshold rings show current BUY/SELL action boundaries.
    buy_r = r_max * BUY_THRESHOLD / 100
    sell_r = r_max * SELL_THRESHOLD / 100
    buy_pts = " ".join(f"{vtx(a, buy_r)[0]:.1f},{vtx(a, buy_r)[1]:.1f}" for a in angles)
    sell_pts = " ".join(f"{vtx(a, sell_r)[0]:.1f},{vtx(a, sell_r)[1]:.1f}" for a in angles)
    thresholds = (
        f'<polygon points="{buy_pts}" fill="none" stroke="#00ff87" stroke-width="0.5" '
        f'stroke-dasharray="3,3" opacity="0.2"/>\n'
        f'<polygon points="{sell_pts}" fill="none" stroke="#ff4d4d" stroke-width="0.5" '
        f'stroke-dasharray="3,3" opacity="0.2"/>\n'
    )

    return (
        f'<svg viewBox="0 0 {vb} {vb}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;max-width:200px">\n'
        f'{grid}{axes}{thresholds}{polygon}{dots}{label_els}'
        f'</svg>'
    )


def threshold_bar_svg(confidence: float, decision: str) -> str:
    """Thin horizontal bar showing confidence position between SELL and BUY thresholds."""
    w, h = 160, 18
    bar_y = 4
    bar_h = 4
    range_min, range_max = 30, 70
    pos = max(0, min(w, (confidence - range_min) / (range_max - range_min) * w))
    sell_pos = (SELL_THRESHOLD - range_min) / (range_max - range_min) * w
    buy_pos = (BUY_THRESHOLD - range_min) / (range_max - range_min) * w
    color = {"BUY": "#00ff87", "SELL": "#ff4d4d", "WATCH": "#f5a623"}.get(decision, "#2a3d52")

    return (
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:{w}px">'
        f'<rect x="0" y="{bar_y}" width="{w}" height="{bar_h}" rx="2" fill="rgba(255,255,255,0.03)"/>'
        f'<rect x="0" y="{bar_y}" width="{sell_pos:.1f}" height="{bar_h}" rx="2" fill="rgba(255,77,77,0.08)"/>'
        f'<rect x="{buy_pos:.1f}" y="{bar_y}" width="{w - buy_pos:.1f}" height="{bar_h}" rx="2" fill="rgba(0,255,135,0.08)"/>'
        f'<line x1="{sell_pos:.1f}" y1="{bar_y - 1}" x2="{sell_pos:.1f}" y2="{bar_y + bar_h + 1}" '
        f'stroke="#ff4d4d" stroke-width="1" opacity="0.4"/>'
        f'<line x1="{buy_pos:.1f}" y1="{bar_y - 1}" x2="{buy_pos:.1f}" y2="{bar_y + bar_h + 1}" '
        f'stroke="#00ff87" stroke-width="1" opacity="0.4"/>'
        f'<circle cx="{pos:.1f}" cy="{bar_y + bar_h / 2}" r="4" fill="{color}" '
        f'style="filter:drop-shadow(0 0 4px {color})"/>'
        f'<text x="{sell_pos:.1f}" y="{bar_y + bar_h + 10}" text-anchor="middle" fill="#ff4d4d" '
        f'font-family="Share Tech Mono,monospace" font-size="5" opacity="0.4">SELL</text>'
        f'<text x="{buy_pos:.1f}" y="{bar_y + bar_h + 10}" text-anchor="middle" fill="#00ff87" '
        f'font-family="Share Tech Mono,monospace" font-size="5" opacity="0.4">BUY</text>'
        f'</svg>'
    )


def render_breakdown_bars(rows: list[dict]) -> str:
    parts = []
    for r in rows:
        c = score_color(r["score"])
        parts.append(f"""
        <div class="bd-item">
            <div class="bd-header">
                <span class="bd-skill">{html.escape(r['skill']).upper()}</span>
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
            <div class="summary-tag" style="color:{c}">{html.escape(r['skill']).upper()} &middot; {r['weight']}% &middot; SCORE {r['score']}</div>
            <div class="summary-text">{safe}</div>
        </div>""")
    return "".join(parts)


# ─── Page Setup ───────────────────────────────────────────────────

st.set_page_config(
    page_title="Sentinel Council",
    page_icon="◎",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st_autorefresh(interval=REFRESH_SECONDS * 1000, key="sc_autorefresh")
st.markdown(CSS, unsafe_allow_html=True)

# ── Masthead ──────────────────────────────────────────────────────

df = load_trades()
now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M UTC")
cycle_label, cycle_value, cycle_color = next_cycle_countdown(df)

st.markdown(f"""
<div class="sc-masthead">
    <div>
        <div class="sc-wordmark">SENTINEL <em>COUNCIL</em></div>
        <div class="sc-tagline">FIVE ANALYSTS &middot; ONE CONSENSUS &middot; ZERO EMOTION &nbsp;&nbsp;|&nbsp;&nbsp; BITGET AGENT HUB</div>
    </div>
    <div class="sc-live-block">
        <div class="sc-live"><span class="pulse-dot"></span> LIVE</div>
        <div class="sc-clock">{now_utc}</div>
        <div class="sc-countdown" style="color:{cycle_color}">{cycle_label} &nbsp;{cycle_value}</div>
    </div>
</div>
""", unsafe_allow_html=True)

# ── How It Works ──────────────────────────────────────────────────

st.markdown("""
<div class="hiw-section">
    <div class="hiw-title">HOW IT WORKS</div>
    <div class="hiw-flow">
        <div class="hiw-analysts">
            <div class="hiw-pill">◎ MACRO ANALYST <span class="hiw-pill-weight">30%</span></div>
            <div class="hiw-pill">▲ TECHNICAL ANALYST <span class="hiw-pill-weight">30%</span></div>
            <div class="hiw-pill">◆ SENTIMENT ANALYST <span class="hiw-pill-weight">20%</span></div>
            <div class="hiw-pill">● NEWS ANALYST <span class="hiw-pill-weight">10%</span></div>
            <div class="hiw-pill">◈ INTEL ANALYST <span class="hiw-pill-weight">10%</span></div>
        </div>
        <div class="hiw-arrow">➡</div>
        <div class="hiw-box hiw-box-main">
            <div class="hiw-box-title">COUNCIL</div>
            <div class="hiw-box-sub">WEIGHTED VOTE</div>
        </div>
        <div class="hiw-arrow">➡</div>
        <div class="hiw-box">
            <div class="hiw-box-title">RISK</div>
            <div class="hiw-box-sub">POSITION SIZING</div>
        </div>
        <div class="hiw-arrow">➡</div>
        <div class="hiw-box">
            <div class="hiw-box-title">EXECUTE</div>
            <div class="hiw-box-sub">SIMULATED TRADE</div>
        </div>
    </div>
    <div class="hiw-rules">
        <em class="r-buy">≥ 72% → BUY</em> &nbsp;&nbsp;│&nbsp;&nbsp;
        <em class="r-sell">≤ 28% → SELL</em> &nbsp;&nbsp;│&nbsp;&nbsp;
        <em class="r-watch">60–71 / 29–40 → WATCH</em> &nbsp;&nbsp;│&nbsp;&nbsp;
        <em class="r-wait">41–59 → WAIT</em> &nbsp;&nbsp;│&nbsp;&nbsp;
        Each analyst scores 0–100 independently using real Bitget data
    </div>
</div>
""", unsafe_allow_html=True)

# ── Live Price Strip ──────────────────────────────────────────────

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
            <div class="price-sym">{label}</div>
            <div class="price-val">{price_fmt}</div>
        </div>
        <div class="price-chg" style="color:{chg_color}">{chg_sign} {abs(chg):.2f}%</div>
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
    # ── Load Latest Cycle Data ────────────────────────────────────
    cycle_data = load_latest_cycle(df)
    cycle_map = {c["symbol"]: c for c in cycle_data}
    ranked_symbols = [c["symbol"] for c in cycle_data]
    for _s in SYMBOLS:
        if _s not in ranked_symbols:
            ranked_symbols.append(_s)
    status_label, status_badge, status_desc = determine_system_status(cycle_data)
    top_opp = next((c for c in cycle_data if c["top_opportunity"]), None)

    # ── Cycle State Banner ────────────────────────────────────────
    top_opp_html = ""
    if top_opp:
        _dir_arrow = {"toward_buy": "↗", "toward_sell": "↘"}.get(top_opp["direction"], "→")
        _dir_color = {"toward_buy": "#00ff87", "toward_sell": "#ff4d4d"}.get(top_opp["direction"], "#2a3d52")
        _dec_color = decision_color(top_opp["decision"])
        top_opp_html = (
            f'<div class="cycle-top-opp">'
            f'<div class="cycle-top-label">◎ TOP OPPORTUNITY</div>'
            f'<div class="cycle-top-sym" style="color:{_dec_color}">{top_opp["label"]}</div>'
            f'<div class="cycle-top-dist" style="color:{_dir_color}">'
            f'{_dir_arrow} {top_opp["distance"]:.1f} pts &middot; {top_opp["direction"].replace("_", " ").upper()}</div>'
            f'</div>'
        )

    st.markdown(
        f'<div class="cycle-banner">'
        f'<div class="cycle-status">'
        f'<span class="cycle-status-badge {status_badge}">{status_label}</span>'
        f'<span class="cycle-status-desc">{status_desc}</span>'
        f'</div>'
        f'{top_opp_html}'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── System Message Panel ──────────────────────────────────────
    _watch_n = sum(1 for c in cycle_data if c["decision"] == "WATCH")
    _wait_n = sum(1 for c in cycle_data if c["decision"] == "WAIT")
    _buy_n = sum(1 for c in cycle_data if c["decision"] == "BUY")
    _sell_n = sum(1 for c in cycle_data if c["decision"] == "SELL")
    _parts: list[str] = []
    if _buy_n:
        _parts.append(f'<span style="color:#00ff87">{_buy_n} BUY</span>')
    if _sell_n:
        _parts.append(f'<span style="color:#ff4d4d">{_sell_n} SELL</span>')
    if _watch_n:
        _parts.append(f'<span style="color:#f5a623">{_watch_n} WATCH</span>')
    if _wait_n:
        _parts.append(f'<span style="color:#3d5166">{_wait_n} WAIT</span>')
    _summary = " &middot; ".join(_parts) if _parts else "No data"

    _has_exec = any(c["action"] in ("OPEN_LONG", "OPEN_SHORT") for c in cycle_data)
    _exec_text = "Trades executed this cycle" if _has_exec else "No executions in current cycle"
    _exec_color = "#00ff87" if _has_exec else "#3d5166"

    _top_headline = ""
    if top_opp:
        _top_headline = html.escape(top_opp["explanation"].split("\n")[0].strip()[:140])

    st.markdown(
        f'<div class="sys-panel">'
        f'<div><div class="sys-item-label">CYCLE DECISIONS</div>'
        f'<div class="sys-item-text">{_summary}</div></div>'
        f'<div><div class="sys-item-label">EXECUTION STATUS</div>'
        f'<div class="sys-item-text" style="color:{_exec_color}">{_exec_text}</div></div>'
        f'<div><div class="sys-item-label">TOP SIGNAL</div>'
        f'<div class="sys-item-text">{_top_headline if _top_headline else "—"}</div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

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

    # ── THE COUNCIL — Pentagon Radar Charts ───────────────────────
    st.markdown('<div class="sc-section">THE COUNCIL — RANKED MARKET VIEW</div>', unsafe_allow_html=True)

    council_cards = ""
    for sym in ranked_symbols:
        label = SYM_LABELS.get(sym, sym)
        sym_df = df[df["symbol"] == sym].sort_values("timestamp")
        _cd = cycle_map.get(sym, {})
        _is_top = _cd.get("top_opportunity", False) if _cd else False
        _card_rank = _cd.get("rank", 0) if _cd else 0
        _card_dist = _cd.get("distance", 0) if _cd else 0
        _card_dir = _cd.get("direction", "neutral") if _cd else "neutral"

        if sym_df.empty:
            council_cards += f"""
            <div class="council-card">
                <div class="council-sym">{label} / USDT</div>
                <div style="text-align:center;padding:2rem 0;font-family:'Share Tech Mono',monospace;font-size:.62rem;color:#1a2a38;letter-spacing:.12em">AWAITING DATA</div>
            </div>"""
            continue

        # Prefer cycle analysis row over CLOSE/manual rows
        if sym in cycle_map:
            _ce = cycle_map[sym]
            confidence = _ce["confidence"]
            decision = _ce["decision"]
            _, skill_rows = parse_explanation(_ce["explanation"])
        else:
            _analysis = sym_df[sym_df.get("action", pd.Series(dtype=str)) != "CLOSE"]
            latest = _analysis.iloc[-1] if not _analysis.empty else sym_df.iloc[-1]
            confidence = float(latest.get("confidence", 0))
            decision = str(latest.get("decision", "WAIT"))
            _, skill_rows = parse_explanation(str(latest.get("explanation", "")))
        score_map = {r["skill"]: r["score"] for r in skill_rows}
        conf_color = score_color(int(confidence))
        prox_text, prox_color = threshold_proximity(confidence, decision)

        radar = council_radar_svg(score_map, confidence, decision)
        tbar = threshold_bar_svg(confidence, decision)

        # Get dominant analyst insight
        _expl_text = cycle_map[sym]["explanation"] if sym in cycle_map else str(sym_df.iloc[-1].get("explanation", ""))
        dom = dominant_skill(_expl_text)
        dom_insight = ""
        for r in skill_rows:
            if r["skill"].upper() == dom and r["summary"] and r["summary"] != "No data":
                dom_insight = r["summary"][:90]
                break
        if not dom_insight and skill_rows:
            for r in skill_rows:
                if r["summary"] and r["summary"] != "No data":
                    dom_insight = r["summary"][:90]
                    break

        _top_cls = " top-opp" if _is_top else ""
        _top_badge = '<div class="top-opp-badge">◎ CLOSEST TO EXECUTION THRESHOLD</div>' if _is_top else ""
        _rank_html = f'<span class="rank-badge">#<span class="rank-num">{_card_rank}</span></span> ' if _card_rank > 0 else ""
        _da = {"toward_buy": "↗", "toward_sell": "↘"}.get(_card_dir, "→")
        _dc = {"toward_buy": "dir-toward-buy", "toward_sell": "dir-toward-sell"}.get(_card_dir, "dir-neutral")
        _dir_html = (
            f'<div class="dir-line {_dc}">{_da} {_card_dist:.1f} PTS &middot; '
            f'{_card_dir.replace("_", " ").upper()}</div>'
        ) if _card_rank > 0 else ""

        _insight_html = f'<div class="council-insight">{html.escape(dom_insight)}</div>' if dom_insight else ""
        council_cards += (
            f'<div class="council-card{_top_cls}">'
            f'{_top_badge}'
            f'<div class="council-sym">{_rank_html}{label} / USDT</div>'
            f'<div class="council-radar">{radar}</div>'
            f'<div style="display:flex;justify-content:center;margin:0.1rem 0 0.2rem">{tbar}</div>'
            f'<div class="council-verdict">'
            f'<span class="council-decision {decision_card_cls(decision)}">{decision}</span>'
            f'<span class="council-conf" style="color:{conf_color}">{confidence:.1f}%</span>'
            f'</div>'
            f'<div class="council-proximity" style="color:{prox_color}">{prox_text}</div>'
            f'{_dir_html}'
            f'{_insight_html}'
            f'</div>'
        )

    st.markdown(f'<div class="council-grid">{council_cards}</div>', unsafe_allow_html=True)

    # ── Signal Intelligence Matrix ─────────────────────────────────
    st.markdown('<div class="sc-section">SIGNAL INTELLIGENCE MATRIX</div>', unsafe_allow_html=True)

    def _cell_style(score: int) -> str:
        if score >= 60:
            return "background:rgba(0,255,135,0.13);color:#00ff87"
        if score < 40:
            return "background:rgba(255,77,77,0.13);color:#ff4d4d"
        return "background:rgba(245,166,35,0.09);color:#f5a623"

    def _conf_style(decision: str) -> str:
        return {
            "BUY":  "background:rgba(0,255,135,0.15);color:#00ff87",
            "SELL": "background:rgba(255,77,77,0.15);color:#ff4d4d",
        }.get(decision, "background:rgba(255,255,255,0.03);color:#2a3d52")

    header_cells = "".join(
        f'<th class="matrix-th">{_A_LABEL[a]}<br>'
        f'<span style="font-size:.48rem;color:#1a2a38">{_A_WEIGHT[a]}</span></th>'
        for a in _ANALYSTS
    )
    matrix_header = (
        f'<tr><th class="matrix-th matrix-th-left">#</th>'
        f'<th class="matrix-th matrix-th-left">SYMBOL</th>'
        f'{header_cells}'
        f'<th class="matrix-th">CONF</th>'
        f'<th class="matrix-th">DECISION</th>'
        f'<th class="matrix-th">DIST</th></tr>'
    )

    matrix_rows = ""
    for sym in ranked_symbols:
        sym_df = df[df["symbol"] == sym].sort_values("timestamp")
        label = SYM_LABELS.get(sym, sym)
        _mr = cycle_map.get(sym, {}).get("rank", 0) if cycle_data else 0
        _rank_cell = f'<td class="matrix-sym" style="color:#3d5166;font-size:.65rem">{_mr}</td>' if _mr > 0 else '<td class="matrix-sym" style="color:#1a2a38">—</td>'
        _md = cycle_map.get(sym, {})
        _m_dist = _md.get("distance", 0) if _md else 0
        _m_dir = _md.get("direction", "neutral") if _md else "neutral"
        _m_dir_c = {"toward_buy": "rgba(0,255,135,0.5)", "toward_sell": "rgba(255,77,77,0.5)"}.get(_m_dir, "#2a3d52")

        if sym_df.empty:
            empty = '<td class="matrix-cell" style="background:rgba(255,255,255,0.02);color:#1a2a38">—</td>'
            matrix_rows += (
                f'<tr>{_rank_cell}<td class="matrix-sym">{label}</td>'
                f'{"".join(empty for _ in _ANALYSTS)}'
                f'<td class="matrix-conf" style="background:rgba(255,255,255,0.02);color:#1a2a38">—</td>'
                f'<td class="matrix-dec" style="color:#1a2a38">—</td>'
                f'<td class="matrix-cell" style="color:#1a2a38">—</td></tr>'
            )
            continue

        if sym in cycle_map:
            _ce2 = cycle_map[sym]
            confidence = _ce2["confidence"]
            decision = _ce2["decision"]
            _, skill_rows = parse_explanation(_ce2["explanation"])
        else:
            _analysis2 = sym_df[sym_df.get("action", pd.Series(dtype=str)) != "CLOSE"]
            latest = _analysis2.iloc[-1] if not _analysis2.empty else sym_df.iloc[-1]
            confidence = float(latest.get("confidence", 0))
            decision = str(latest.get("decision", "WAIT"))
            _, skill_rows = parse_explanation(str(latest.get("explanation", "")))
        score_map = {r["skill"]: r["score"] for r in skill_rows}
        dec_color = decision_color(decision)
        if dec_color == "#2a3d52":
            dec_color = "#5e7a94"

        analyst_cells = "".join(
            f'<td class="matrix-cell" style="{_cell_style(score_map.get(a, 0))}">'
            f'{score_map.get(a, "—")}</td>'
            for a in _ANALYSTS
        )
        _dist_txt = f'{_m_dist:.1f}' if _mr > 0 else "—"
        matrix_rows += (
            f'<tr>{_rank_cell}<td class="matrix-sym">{label}</td>'
            f'{analyst_cells}'
            f'<td class="matrix-conf" style="{_conf_style(decision)}">{confidence:.0f}</td>'
            f'<td class="matrix-dec" style="color:{dec_color}">{decision}</td>'
            f'<td class="matrix-cell" style="color:{_m_dir_c}">{_dist_txt}</td></tr>'
        )

    st.markdown(
        f'<div class="matrix-wrap"><table class="matrix-table">'
        f'<thead>{matrix_header}</thead><tbody>{matrix_rows}</tbody>'
        f'</table></div>',
        unsafe_allow_html=True,
    )

    # ── Open Positions Monitor ─────────────────────────────────────
    open_positions = load_open_positions() or {}
    st.markdown('<div class="sc-section">OPEN POSITIONS</div>', unsafe_allow_html=True)

    if open_positions:
        pos_cards = ""
        for sym, pos in open_positions.items():
            entry = float(pos.get("entry_price", 0))
            size = float(pos.get("size", 0))
            live_p = prices.get(sym, {}).get("price", 0.0)
            label = SYM_LABELS.get(sym, sym)

            if live_p > 0 and entry > 0:
                pnl_pct = (live_p - entry) / entry * 100
                pnl_usd = (live_p - entry) / entry * size
                pnl_sign = "+" if pnl_pct >= 0 else ""
                pnl_cls = "pos-pnl-pos" if pnl_pct >= 0 else "pos-pnl-neg"
                usd_color = "#00ff87" if pnl_usd >= 0 else "#ff4d4d"
                pnl_block = (
                    f'<span class="{pnl_cls}">{pnl_sign}{pnl_pct:.2f}%</span>'
                    f'<span style="font-family:\'Share Tech Mono\',monospace;font-size:.62rem;'
                    f'color:{usd_color};margin-left:.5rem">{pnl_sign}${abs(pnl_usd):.2f}</span>'
                )
                current_str = f"${live_p:,.4f}"
            else:
                pnl_block = '<span style="color:#2a3d52">PRICE UNAVAILABLE</span>'
                current_str = "—"

            sl_price = f"${entry * (1 + SL_PCT):,.4f}" if entry > 0 else "—"
            tp_price = f"${entry * (1 + TP_PCT):,.4f}" if entry > 0 else "—"
            opened = str(pos.get("opened_at", ""))[:16].replace("T", " ")

            pos_cards += f"""
            <div class="pos-card">
                <div class="pos-sym">◎ {label} / USDT</div>
                <div class="pos-row">
                    <span class="pos-label">UNREALIZED P&L</span>
                    <span>{pnl_block}</span>
                </div>
                <hr class="pos-divider"/>
                <div class="pos-row">
                    <span class="pos-label">ENTRY</span>
                    <span class="pos-val">${entry:,.4f}</span>
                </div>
                <div class="pos-row">
                    <span class="pos-label">CURRENT</span>
                    <span class="pos-val">{current_str}</span>
                </div>
                <div class="pos-row">
                    <span class="pos-label">SIZE</span>
                    <span class="pos-val">${size:.2f}</span>
                </div>
                <hr class="pos-divider"/>
                <div class="pos-row">
                    <span class="pos-label" style="color:rgba(255,77,77,.5)">STOP LOSS</span>
                    <span style="font-family:'Share Tech Mono',monospace;font-size:.65rem;color:rgba(255,77,77,.5)">{sl_price}</span>
                </div>
                <div class="pos-row">
                    <span class="pos-label" style="color:rgba(0,255,135,.4)">TAKE PROFIT</span>
                    <span style="font-family:'Share Tech Mono',monospace;font-size:.65rem;color:rgba(0,255,135,.4)">{tp_price}</span>
                </div>
                <div style="font-family:'Share Tech Mono',monospace;font-size:.52rem;color:#1a2a38;margin-top:.5rem;letter-spacing:.06em">OPENED {html.escape(opened)} UTC</div>
            </div>"""
        st.markdown(f'<div class="pos-grid">{pos_cards}</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            '<div class="pos-none">NO OPEN POSITIONS &nbsp;·&nbsp; MONITORING MARKETS</div>',
            unsafe_allow_html=True,
        )

    # ── Equity Curve ───────────────────────────────────────────────
    st.markdown('<div class="sc-section">EQUITY CURVE</div>', unsafe_allow_html=True)

    eq_cols = ["timestamp", "balance"] + (["action"] if "action" in df.columns else [])
    eq_df = df[eq_cols].copy()
    if "action" in eq_df.columns:
        eq_df = eq_df[eq_df["action"].isin(["OPEN_LONG", "CLOSE"])]
    if eq_df.empty:
        eq_df = df[["timestamp", "balance"]].copy()
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
    st.altair_chart(equity_chart, use_container_width=True)

    # ── Confidence Trend ───────────────────────────────────────────
    st.markdown('<div class="sc-section">CONFIDENCE TREND</div>', unsafe_allow_html=True)

    SYM_COLORS = {
        "BTC": "#f5a623", "ETH": "#4d9eff", "SOL": "#c084fc",
        "BGB": "#00e5ff", "AVAX": "#ff6b35", "DOGE": "#c8a800",
    }
    trend_df = df[["timestamp", "symbol", "confidence"]].copy()
    trend_df["sym_label"] = trend_df["symbol"].map(SYM_LABELS).fillna(trend_df["symbol"])
    _sym_labels_ordered = [SYM_LABELS.get(s, s) for s in SYMBOLS]

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
            color=alt.Color("sym_label:N",
                scale=alt.Scale(
                    domain=list(SYM_COLORS.keys()),
                    range=list(SYM_COLORS.values()),
                ),
                legend=alt.Legend(
                    labelColor="#5e7a94", titleColor="#2a3d52",
                    labelFont="Share Tech Mono", labelFontSize=10,
                    title="SYMBOL", orient="top-right",
                )),
            tooltip=[
                alt.Tooltip("timestamp:T", title="Time"),
                alt.Tooltip("sym_label:N", title="Symbol"),
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
    sell_label = (
        alt.Chart(pd.DataFrame({"y": [SELL_THRESHOLD]}))
        .mark_text(text="SELL", align="right", baseline="top", dx=-6, dy=2,
                   color="#ff4d4d", opacity=0.5, fontSize=9, font="Share Tech Mono")
        .encode(y="y:Q")
    )
    trend_chart = (
        (lines + buy_rule + buy_label + sell_rule + sell_label)
        .properties(height=240, background="#070b0f",
                    padding={"top": 16, "bottom": 16, "left": 16, "right": 16})
        .configure_view(strokeWidth=0, fill="#070b0f")
    )
    st.altair_chart(trend_chart, use_container_width=True)

    # ── Skill Breakdown (Tabs) ─────────────────────────────────────
    st.markdown('<div class="sc-section">ANALYST REASONING</div>', unsafe_allow_html=True)

    tabs = st.tabs([f"◎ {SYM_LABELS.get(s, s)}" for s in SYMBOLS])

    for symbol, tab in zip(SYMBOLS, tabs):
        with tab:
            sym_df = df[df["symbol"] == symbol].sort_values("timestamp")
            if sym_df.empty:
                st.markdown(
                    '<div class="sc-empty" style="padding:2rem">'
                    'AWAITING FIRST CYCLE</div>',
                    unsafe_allow_html=True,
                )
                continue

            if symbol in cycle_map:
                _ce3 = cycle_map[symbol]
                confidence = _ce3["confidence"]
                decision = _ce3["decision"]
                headline, skill_rows = parse_explanation(_ce3["explanation"])
            else:
                _analysis3 = sym_df[sym_df.get("action", pd.Series(dtype=str)) != "CLOSE"]
                latest = _analysis3.iloc[-1] if not _analysis3.empty else sym_df.iloc[-1]
                headline, skill_rows = parse_explanation(str(latest.get("explanation", "")))
                decision = str(latest.get("decision", "WAIT"))
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

    # ── Decision Log ───────────────────────────────────────────────
    st.markdown('<div class="sc-section">DECISION LOG</div>', unsafe_allow_html=True)

    timestamps = df["timestamp"]
    hours_span = round((timestamps.max() - timestamps.min()).total_seconds() / 3600, 1)
    buy_c = int((df["decision"] == "BUY").sum())
    sell_c = int((df["decision"] == "SELL").sum())
    watch_c = int((df["decision"] == "WATCH").sum())
    wait_c = int((df["decision"] == "WAIT").sum())

    st.markdown(f"""
    <div style="font-family:'Share Tech Mono',monospace;font-size:.62rem;color:#2a3d52;
                letter-spacing:.12em;margin-bottom:.75rem">
        {len(df)} DECISIONS &nbsp;·&nbsp; {hours_span}H SPAN &nbsp;·&nbsp;
        <span style="color:#00ff87">{buy_c} BUY</span> &nbsp;
        <span style="color:#ff4d4d">{sell_c} SELL</span> &nbsp;
        <span style="color:#f5a623">{watch_c} WATCH</span> &nbsp;
        <span style="color:#2a3d52">{wait_c} WAIT</span>
    </div>""", unsafe_allow_html=True)

    recent = df.sort_values("timestamp", ascending=False).head(20)
    rows_html = ""
    for _, row in recent.iterrows():
        dec = str(row.get("decision", "WAIT"))
        ts = str(row.get("timestamp", ""))[:19].replace("T", " ")
        total_pnl_v = float(row.get("pnl", 0))
        trade_pnl_v = float(row.get("trade_pnl", 0))
        rows_html += f"""
        <tr>
            <td class="ts-dim">{html.escape(ts)}</td>
            <td>{html.escape(str(row.get('symbol', '')))}</td>
            <td class="{decision_cls(dec)}">{dec}</td>
            <td style="color:#5e7a94">{float(row.get('confidence', 0)):.1f}%</td>
            <td style="color:#2a3d52">{html.escape(str(row.get('action', '')))}</td>
            <td style="color:#5e7a94">${float(row.get('balance', 0)):,.2f}</td>
            <td style="color:{'#00ff87' if trade_pnl_v >= 0 else '#ff4d4d'}">{trade_pnl_v:+.4f}</td>
            <td style="color:{'#00ff87' if total_pnl_v >= 0 else '#ff4d4d'}">{total_pnl_v:+.4f}</td>
        </tr>"""

    st.markdown(f"""
    <div class="audit-wrap">
    <table class="audit-table">
        <thead><tr>
            <th>TIMESTAMP</th><th>SYMBOL</th><th>DECISION</th>
            <th>CONFIDENCE</th><th>ACTION</th><th>BALANCE</th><th>TRADE P&amp;L</th><th>TOTAL P&amp;L</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
    </table></div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    try:
        audit_bytes = TRADES_CSV.read_bytes()
    except Exception:
        audit_bytes = b""
    st.download_button(
        "↓  EXPORT AUDIT CSV",
        data=audit_bytes,
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
    ticker_text = "  &nbsp;&nbsp;&nbsp;◎&nbsp;&nbsp;&nbsp; ".join(
        f"<em>{html.escape(h)}</em>" for h in ticker_news
    )
    ticker_double = f"{ticker_text}  &nbsp;&nbsp;&nbsp;◎&nbsp;&nbsp;&nbsp;  {ticker_text}"
    st.markdown(f"""
    <div class="ticker-wrap">
        <div class="ticker-badge">◎ CRYPTO NEWS</div>
        <div class="ticker-track">
            <span class="ticker-inner">{ticker_double}</span>
        </div>
    </div>""", unsafe_allow_html=True)
