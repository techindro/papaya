import time
import requests
import streamlit as st
import pandas as pd
import numpy as np
import psutil

st.set_page_config(page_title="Papaya Sovereign Node Dashboard", layout="wide")

st.title("Papaya Live API Nodes Dashboard")
st.subheader("Automated Telemetry Tracking and Machine Micro-Payments")

col1, col2 = st.columns(2)

with col1:
    st.header("Live Resource Extraction")
    cpu_load = float(psutil.cpu_percent(interval=0.5))
    mem_load = float(psutil.virtual_memory().percent)
    tokens_processed = int(np.random.randint(50, 1500))
    
    st.metric("Current CPU Load", f"{cpu_load} %")
    st.metric("Memory Consumption", f"{mem_load} %")
    st.metric("Active Token Stream Count", f"{tokens_processed} Tokens")
    
    if st.button("Trigger Autonomous Payment Settlement", type="primary"):
        with st.spinner("Processing background settlement pipeline..."):
            payload = {
                "cpu_usage": cpu_load,
                "memory_usage": mem_load,
                "token_stream_count": tokens_processed
            }
            try:
                response = requests.post("http://localhost:8000/api/compute/settle", json=payload, timeout=12.0)
                if response.status_code == 200:
                    data = response.json()
                    st.success("Transaction Settled Autonomously!")
                    st.json(data)
                else:
                    st.error(f"Settlement Failed: {response.text}")
            except Exception as e:
                st.error(f"Unable to reach Backend Gateway: {str(e)}")

with col2:
    st.header("Real-time Workspace Telemetry Logs")
    chart_data = pd.DataFrame(
        np.random.randn(20, 2),
        columns=['CPU Load Tracker', 'Network Speed Coefficient']
    )
    st.line_chart(chart_data)
    
    st.header("Sovereign Node Text Logs")
    st.text(f"[{time.strftime('%X')}] [INFO] Core Telemetry Sensor Engine Active...")
    st.text(f"[{time.strftime('%X')}] [METRIC] System Raw Payload captured.")
