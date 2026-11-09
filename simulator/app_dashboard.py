"""Papaya dashboard (Streamlit).

Start the backend first, then run from the project root:
    streamlit run simulator/app_dashboard.py
"""

from __future__ import annotations

import os
import time
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

DEFAULT_BACKEND = os.getenv("PAPAYA_BACKEND_URL", "http://localhost:8000")
API_KEY = os.getenv("PAPAYA_API_KEY", "")
TIMEOUT = 15

st.set_page_config(page_title="Papaya Dashboard", page_icon="🥭", layout="wide")

if "history" not in st.session_state:
    st.session_state.history = []  # one dict per billing cycle


# ---------------------------------------------------------------------------
# Backend helpers (errors are shown in the UI, never crash the page)
# ---------------------------------------------------------------------------


def api_get(base: str, path: str, params: dict | None = None):
    try:
        resp = requests.get(f"{base}{path}", params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        st.error(f"Backend request failed ({path}): {exc}")
        return None


def api_post(base: str, path: str, payload: dict):
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    try:
        resp = requests.post(f"{base}{path}", json=payload, headers=headers, timeout=TIMEOUT)
        if resp.status_code >= 400:
            st.error(f"Backend rejected {path}: {resp.status_code} {resp.text[:200]}")
            return None
        return resp.json()
    except requests.RequestException as exc:
        st.error(f"Backend request failed ({path}): {exc}")
        return None


def run_cycle(base: str, node_id: str, window: float) -> None:
    """Sample telemetry, then price and pay it."""
    sample = api_get(base, "/telemetry/sample", {"node_id": node_id, "window_seconds": window})
    if not sample:
        return
    record = api_post(base, "/pay", sample)
    if not record:
        return
    st.session_state.history.append(
        {
            "time": datetime.now().strftime("%H:%M:%S"),
            "cpu": sample["cpu_percent"],
            "memory": sample["memory_percent"],
            "tokens": sample["tokens"],
            "amount": float(record["amount"]),
            "status": record["status"],
            "mode": record["mode"],
        }
    )


# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------

st.sidebar.title("🥭 Papaya")
backend = st.sidebar.text_input("Backend URL", DEFAULT_BACKEND).rstrip("/")
node_id = st.sidebar.text_input("Node ID", "local-node")
window = st.sidebar.slider("Billing window (seconds)", 1, 60, 5)
auto = st.sidebar.toggle("Auto-run cycles", value=False)
interval = st.sidebar.slider("Auto-run interval (seconds)", 2, 30, 5, disabled=not auto)
manual = st.sidebar.button("Run one cycle", type="primary", width="stretch")
if st.sidebar.button("Clear history", width="stretch"):
    st.session_state.history = []

if manual or auto:
    run_cycle(backend, node_id, float(window))

# ---------------------------------------------------------------------------
# Header + backend status
# ---------------------------------------------------------------------------

st.title("Papaya Billing Dashboard")
st.caption("Machines track their own workload and pay for it through the PayPal Sandbox.")

health = api_get(backend, "/health")
if health:
    c1, c2, c3 = st.columns(3)
    c1.metric("Backend", "Online")
    c2.metric("PayPal Sandbox", "Configured" if health["paypal_configured"] else "Not configured")
    c3.metric("Stored payment source", "Yes" if health["vault_configured"] else "No")
    if not health["paypal_configured"]:
        st.warning(
            "PayPal credentials are not set on the backend. Prices are calculated "
            "locally and no payment is made."
        )

# ---------------------------------------------------------------------------
# Metrics + charts
# ---------------------------------------------------------------------------

history = st.session_state.history
df = pd.DataFrame(history)

if df.empty:
    st.info("Click **Run one cycle** in the sidebar to measure this machine and bill it.")
else:
    last = df.iloc[-1]
    paid = df[df["status"] == "COMPLETED"]["amount"].sum()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("CPU load", f"{last['cpu']:.1f}%")
    m2.metric("Data tokens", f"{int(last['tokens']):,}")
    m3.metric("Last charge", f"${last['amount']:.2f}")
    m4.metric("Paid so far (captured)", f"${paid:.2f}")

    left, right = st.columns(2)

    cpu_fig = go.Figure()
    cpu_fig.add_trace(go.Scatter(x=df["time"], y=df["cpu"], mode="lines+markers", name="CPU %"))
    cpu_fig.add_trace(go.Scatter(x=df["time"], y=df["memory"], mode="lines", name="Memory %"))
    cpu_fig.update_layout(title="Server workload", yaxis_range=[0, 100], height=320,
                          margin=dict(l=10, r=10, t=40, b=10))
    left.plotly_chart(cpu_fig, width="stretch")

    cost_fig = go.Figure(go.Bar(x=df["time"], y=df["amount"], name="Charge"))
    cost_fig.update_layout(title="Charge per cycle (USD)", height=320,
                           margin=dict(l=10, r=10, t=40, b=10))
    right.plotly_chart(cost_fig, width="stretch")

# ---------------------------------------------------------------------------
# Payment log + node logs
# ---------------------------------------------------------------------------

st.subheader("Payment log")
txs = api_get(backend, "/transactions", {"limit": 50})
if txs:
    table = pd.DataFrame(txs)[
        ["timestamp", "node_id", "amount", "currency", "status", "mode", "order_id", "detail"]
    ]
    st.dataframe(table, width="stretch", hide_index=True)
else:
    st.caption("No payments yet.")

st.subheader("Sovereign Node Logs")
logs = api_get(backend, "/logs", {"limit": 100})
st.code("\n".join(logs["lines"][::-1]) if logs and logs["lines"] else "No log lines yet.",
        language="text")

# ---------------------------------------------------------------------------
# Auto-run loop
# ---------------------------------------------------------------------------

if auto:
    time.sleep(interval)
    st.rerun()
