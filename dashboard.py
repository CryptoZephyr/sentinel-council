"""
Sentinel Council — Streamlit dashboard.
Reads trades.csv and shows portfolio metrics, recent trades, and the most
recent decision explanation per symbol. Auto-refreshes every 60 seconds.

Run with: streamlit run dashboard.py
"""

import time
from pathlib import Path

import pandas as pd
import streamlit as st

TRADES_CSV = Path("trades.csv")
REFRESH_SECONDS = 60

st.set_page_config(page_title="Sentinel Council", layout="wide")
st.title("Sentinel Council — Trading Agent Dashboard")


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

    st.subheader("Latest Decision Per Symbol")
    latest_per_symbol = df.sort_values("timestamp").groupby("symbol").tail(1)
    for _, row in latest_per_symbol.iterrows():
        with st.expander(f"{row['symbol']} — {row['decision']} @ {row['confidence']:.1f}% confidence"):
            st.code(row["explanation"], language=None)

    st.subheader("20 Most Recent Trades")
    recent = df.sort_values("timestamp", ascending=False).head(20)
    display_cols = ["timestamp", "symbol", "decision", "confidence", "action", "size", "balance", "pnl"]
    st.dataframe(recent[display_cols], width="stretch", hide_index=True)

st.caption(f"Auto-refreshing every {REFRESH_SECONDS} seconds.")
time.sleep(REFRESH_SECONDS)
st.rerun()
