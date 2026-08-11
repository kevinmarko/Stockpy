---
name: incident-triage
description: Procedures and playbook for triaging incidents on the Stockpy platform. Use when investigating a live issue, orchestrator failure, or executing an emergency stop.
---

# Incident Triage Skill

This skill outlines the process for responding to live incidents on the Stockpy quantitative platform.

## 1. Global Kill Switch (Advisory Pause Procedure)

In the event of anomalous trading behavior, data corruption, or severe broker API issues, the FIRST step is to halt live execution.

**Activate the Kill Switch:**
```bash
python -m execution.kill_switch --status
# To activate: create or toggle the kill switch file as dictated by kill_switch.py
```
This forces all order placements and follow executions to be paused immediately.

## 2. Diagnosing Orchestrator Failures

The `main_orchestrator.py` daemon manages all scheduled tasks. If it crashes or loops continuously:
- **Preflight Checks**: Run `python scripts/preflight_check.py` to ensure all configurations (API keys, DB schema) are valid.
- **Log Inspection**: Review the recent `investyo-mcp` service logs or local `.log` files to identify the failing task. 

## 3. Incident Logging

All production incidents MUST be recorded in the incident log.
Append an entry to `docs/incident_log.md` using the standard template:
- **Date & Time**
- **Symptom**
- **Impact**
- **Root Cause**
- **Remediation Steps**

## 4. Common Failure Modes & Fixes

**Failure Mode: Stale Robinhood Credentials**
- **Symptom**: `main_orchestrator.py` or MCP tools fail with a 401 Unauthorized from Robinhood APIs.
- **Fix**: Run `python3 main.py --refresh-account` to force a fresh login and obtain a new auth token.

**Failure Mode: Split-Brain Daemon Execution**
- **Symptom**: Orders are double-submitted or the orchestrator logs show conflicting runs interleaving.
- **Fix**: Identify stray `python` processes running the orchestrator or kill any rogue background daemon tasks. Ensure only one `main_orchestrator.py` instance is running in production.
