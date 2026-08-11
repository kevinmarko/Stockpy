---
name: alert-rule-authoring
description: Authoring and updating custom alert rules for the platform. Use when creating new alert triggers or adjusting existing alert thresholds.
---

# Alert Rule Authoring Skill

This skill explains how to author and configure custom alert rules in Stockpy.

## 1. Alert Architecture

Alerts are evaluated during the platform's orchestration loop (`main_orchestrator.py`).
The MCP tool `configure_alerts` writes these thresholds to a JSON configuration file, which is then parsed by `alerting.py`.

## 2. Testing Alerts

To test an alert trigger without waiting for the next orchestration loop, use the MCP tool:
```bash
# This is typically called via the send_test_alert MCP tool.
```
Or to manually verify the schema of your alerts config:
```bash
make verify
```

## 3. Thresholds & Conventions

When modifying alert thresholds, ensure they use the proper DTO definitions:
- **Drawdown Alert**: Typically triggered when account equity drops by `> X%`.
- **VIX Alert**: Triggered when VIX crosses a critical threshold (usually `> 30` or `> 35`).
- **Data Freshness**: Alerts on stale account snapshots `> 20.0` hours old (`data/robinhood_portfolio.py`).

## 4. Common Failure Modes & Fixes

**Failure Mode: Alerts Are Spammed Constantly**
- **Symptom:** An alert condition (e.g., VIX > 30) is met, and the orchestrator fires a notification on every loop.
- **Fix:** Implement a cooldown mechanism or an edge-triggered state (only fire when transitioning from VIX <= 30 to VIX > 30) in `alerting.py`.

**Failure Mode: `configure_alerts` Erases Existing Configs**
- **Symptom:** Submitting a partial update to `configure_alerts` deletes unmentioned alert settings.
- **Fix:** Ensure your tool or script performs a deep merge of the new configuration JSON against the existing configuration rather than overwriting it entirely.
