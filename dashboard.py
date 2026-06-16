"""
Sentinel Council — Streamlit dashboard.
Reads trades.csv and shows portfolio metrics, recent trades, and the most
recent decision explanation per symbol. Auto-refreshes every 60 seconds.

Run with: streamlit run dashboard.py
"""

import re
import time
from pathlib import Path

import pandas as pd
import streamlit as st

TRADES_CSV = Path("trades.csv")
REFRESH_SECONDS = 60

DECISION_BADGE = {
    "BUY": ":green[**BUY**]",
    "SELL": ":red[**SELL**]",
    "HOLD": ":gray[**HOLD**]",
}

EXPLANATION_LINE_RE = re.compile(
    r"^\s*-\s*(?P<skill>\w+) \((?P<weight>\d+)% weight, score (?P<score>\d+)\): (?P<summary>.*)$"
)

st.set_page_config(page_title="Sentinel Council", layout="wide")
st.title("Sentinel Council — Trading Agent Dashboard")
st.caption(
    "Five independent analyses (macro, technical, sentiment, news, market-intel) are each "
    "scored 0-100 and combined into one weighted confidence score. **BUY** at confidence >= 70, "
    "**SELL** at confidence <= 35, otherwise **HOLD**. Trades below run against a simulated "
    "$10,000 USDT portfolio using real Bitget prices — no real capital is at risk."
)


def load_trades() -> pd.DataFrame | None:
    """Returns the trades DataFrame, or None if the CSV is missing/empty."""
    if not TRADES_CSV.exists():
        return None
    try:
        df = pd.read_csv(TRADES_CSV)
    except pd.errors.EmptyDataError:
        return None
    if df.empty:
        return None
    return df.sort_values("timestamp").reset_index(drop=True)


def compute_metrics(df: pd.DataFrame) -> dict:
    """Derives balance/PnL/trade-count/win-rate purely from trades.csv.

    trade_count = rows where a position was actually closed (CLOSE action).
    win_rate: pnl is a portfolio-wide running total logged on every row, so
    each CLOSE row's realized PnL is the delta from the previous row's
    cumulative total — a positive delta is a win.
    """
    latest = df.iloc[-1]
    closes = df[df["action"] == "CLOSE"]
    pnl_deltas = df["pnl"].diff().reindex(closes.index)
    trade_count = len(closes)
    win_count = int((pnl_deltas > 0).sum())
    win_rate = round(win_count / trade_count * 100, 1) if trade_count else 0.0
    return {
        "balance": latest["balance"],
        "total_pnl": latest["pnl"],
        "trade_count": trade_count,
        "win_rate": win_rate,
    }


def parse_explanation(explanation: str) -> tuple[str, pd.DataFrame]:
    """Splits the explanation text into a headline and a per-skill score table."""
    lines = str(explanation).split("\n")
    headline = lines[0].strip()
    rows = []
    for line in lines[1:]:
        m = EXPLANATION_LINE_RE.match(line)
        if m:
            rows.append(
                {
                    "Skill": m.group("skill"),
                    "Weight": f"{m.group('weight')}%",
                    "Score": int(m.group("score")),
                    "Summary": m.group("summary"),
                }
            )
    return headline, pd.DataFrame(rows)


df = load_trades()

if df is None:
    st.info(
        "No trades yet — trades.csv is missing or empty. "
        "This dashboard will populate once sentinel.py has run at least one cycle."
    )
else:
    metrics = compute_metrics(df)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Balance", f"${metrics['balance']:,.2f}")
    col2.metric("Total PnL", f"${metrics['total_pnl']:,.4f}")
    col3.metric("Trade Count", metrics["trade_count"])
    col4.metric("Win Rate", f"{metrics['win_rate']:.1f}%")
    st.caption(
        f"{len(df)} logged decisions, spanning {df['timestamp'].min()} to {df['timestamp'].max()}. "
        "Data reflects whatever trades.csv was last committed/pushed — it is a snapshot, not a live "
        "feed, unless this app is reading a CSV updated by a continuously-running sentinel.py."
    )

    st.subheader("Latest Decision Per Symbol")
    symbols = sorted(df["symbol"].unique())
    tabs = st.tabs(symbols)
    for symbol, tab in zip(symbols, tabs):
        with tab:
            sym_df = df[df["symbol"] == symbol].sort_values("timestamp")
            latest = sym_df.iloc[-1]
            headline, breakdown = parse_explanation(latest["explanation"])
            badge = DECISION_BADGE.get(latest["decision"], f"**{latest['decision']}**")

            st.markdown(f"### {symbol} — {badge} @ {latest['confidence']:.1f}% confidence")
            st.write(headline)

            if not breakdown.empty:
                left, right = st.columns([2, 3])
                with left:
                    st.bar_chart(breakdown.set_index("Skill")["Score"])
                with right:
                    st.dataframe(
                        breakdown[["Skill", "Weight", "Score", "Summary"]],
                        width="stretch",
                        hide_index=True,
                    )

            if len(sym_df) > 1:
                st.caption("Confidence over time")
                trend = sym_df.set_index("timestamp")["confidence"]
                st.line_chart(trend)

    st.subheader("20 Most Recent Trades")
    recent = df.sort_values("timestamp", ascending=False).head(20).copy()
    recent["decision"] = recent["decision"].map(lambda d: DECISION_BADGE.get(d, d))
    display_cols = ["timestamp", "symbol", "decision", "confidence", "action", "size", "balance", "pnl"]
    st.dataframe(recent[display_cols], width="stretch", hide_index=True)

st.caption(f"Auto-refreshing every {REFRESH_SECONDS} seconds.")
time.sleep(REFRESH_SECONDS)
st.rerun()
