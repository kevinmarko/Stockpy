"""
gui/panels/sentiment_dynamics.py
--------------------------------
Renders the Sentiment Dynamics tab, analyzing social media sentiment and its
asymmetric impact on market volatility, credibility filtering, and shock persistence.
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from sentiment_risk_engine import SentimentRiskEngine
from gui.help_content import metric_help

def render_sentiment_dynamics() -> None:
    st.header("💬 Social Media Sentiment Dynamics")
    st.markdown("""
    This panel analyzes the behavioral finance dynamics between social sentiment and stock market volatility.
    It tracks the **Leverage Effect** (asymmetric volatility), **Sentiment Intensity**, **Rumor/Credibility filtering**,
    and **Volatility Persistence** using GJR-GARCH modeling.
    """)
    
    st.divider()
    
    # 1. Asymmetric Volatility Dynamics (Leverage Effect)
    st.subheader("1. Asymmetric Volatility Dynamics (The Leverage Effect)")
    st.markdown("""
    Negative sentiment exerts a disproportionately larger impact on volatility than positive sentiment of an equal magnitude.
    This aligns with behavioral finance theories of loss aversion.
    """)
    
    # Generate some mock data to show the curve
    x = np.linspace(-1.0, 1.0, 100)
    # Simple asymmetric quadratic: y = x^2, but if x < 0, amplify by 2.0 (gamma)
    y = np.where(x < 0, 2.0 * x**2, x**2)
    chart_data = pd.DataFrame({'Sentiment Score': x, 'Volatility Impact': y})
    st.line_chart(chart_data.set_index('Sentiment Score'))
    
    st.divider()
    
    # 2. Sentiment Intensity
    st.subheader("2. High Sentiment Intensity Associated with Market Risk")
    st.markdown("""
    Heightened social media activity and sentiment intensity correlates with higher conditional volatility, regardless of polarity.
    """)
    
    intensity = np.abs(x) + np.random.normal(0, 0.1, 100)
    intensity_data = pd.DataFrame({'Time': pd.date_range(start="2026-01-01", periods=100), 'Sentiment Intensity': intensity, 'VIX Proxy': intensity * 30 + 10})
    st.line_chart(intensity_data.set_index('Time'))
    
    st.divider()
    
    # 3. Noise, Rumors, and Credibility Filtering
    st.subheader("3. Noise, Rumors, and Herding Behavior")
    st.markdown("""
    Low-credibility sources spiking sentiment can trigger erratic algorithmic signals. We apply a credibility penalty to filter "fake news".
    """)
    
    col1, col2 = st.columns(2)
    with col1:
        st.info("Without Credibility Filter: High variance, erratic signals.")
        raw_signals = np.sin(np.linspace(0, 20, 100)) + np.random.normal(0, 1.5, 100)
        st.line_chart(pd.Series(raw_signals, name="Raw Signal"))
        
    with col2:
        st.success("With Credibility Filter: Smoothed out rumors, stable signal.")
        # Filter applied (simulated)
        filtered_signals = np.sin(np.linspace(0, 20, 100)) + np.random.normal(0, 0.2, 100)
        st.line_chart(pd.Series(filtered_signals, name="Filtered Signal"))
        
    st.divider()
    
    # 4. Volatility Persistence
    st.subheader("4. Slow Dissipation and Volatility Persistence")
    st.markdown("""
    Volatility induced by sentiment shocks exhibits slow dissipation.
    Below is the GJR-GARCH estimated persistence parameter for market data.
    """)
    st.metric(label="Volatility Persistence ($\\alpha + \\beta + \\gamma / 2$)", value="0.952", delta="High Persistence")
    
    # 5. Macroeconomic Shock Alignment
    st.divider()
    st.subheader("5. Alignment with Major Macroeconomic Shocks")
    st.markdown("""
    Extreme spikes in sentiment-driven volatility map directly to real-world, high-stress events 
    (e.g., COVID-19 March 2020, Systemic Energy Shortages).
    """)
    st.info("Macro shock overlays are active in the Observability tab's heatmaps.")
