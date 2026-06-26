# InvestYo Advisory Platform — Incident Log

Append one entry per incident. Keep entries in reverse-chronological order (newest at top).
Cross-reference the Decision Journal (`output/decision_log.jsonl`) for operator-action
entries written during the incident; those records carry the `"modified"` action type
and are linked to this log by the `incident_id` field.

---

## Incident Template

Copy the block below, fill in each field, and paste at the top of the
"Active & Resolved Incidents" section.

```
### INC-YYYY-MM-DD-NNN — <one-line summary>

| Field            | Value |
|------------------|-------|
| **Incident ID**  | INC-YYYY-MM-DD-NNN |
| **Date / Time**  | YYYY-MM-DD HH:MM UTC |
| **Severity**     | P1 – Critical / P2 – High / P3 – Medium / P4 – Low |
| **Status**       | Open / Monitoring / Resolved |
| **Duration**     | X h Y min (from first symptom to resolution) |
| **Affected Modules** | e.g. `engine/advisory.py`, `data/robinhood_portfolio.py` |
| **Impacted Symbols** | e.g. AAPL, MSFT – or "Universe-wide" |

#### Symptoms

Describe what the operator saw: wrong signal, stale data, error alert, dashboard anomaly.
Paste the relevant log excerpt (from `logs/investyo.log`) below.

```
2026-06-26 09:35:08  ERROR   InvestYo.main — Advisory failed for TSLA: TimeoutError
```

#### Root Cause

One paragraph. What was the underlying technical or data failure?
- Which component?
- Which line / function?
- Why did the guard / fallback not absorb it?

#### Resolution Steps

1. Step taken, with the exact command if applicable.
2. Step taken.
3. …

```bash
# Example command used during resolution
python -m execution.kill_switch --activate --reason "INC-YYYY-MM-DD-NNN investigation"
python3 main.py --refresh-account
python -m execution.kill_switch --deactivate
```

#### Follow-up Actions

- [ ] Action item — owner — due date
- [ ] Update `.env` or config threshold
- [ ] Add test to prevent regression
- [ ] Update RUNBOOK.md §3.X with new playbook

#### Decision Journal Reference

| Timestamp | Symbol | Action | Notes |
|-----------|--------|--------|-------|
| YYYY-MM-DD HH:MM | ALL | modified | *Paste the relevant `output/decision_log.jsonl` line here* |

---
```

---

## Severity Definitions

| Level | Criteria | Response SLA |
|-------|----------|-------------|
| **P1 – Critical** | All signals lost; kill-switch auto-fired; account snapshot missing > 48 h; calibration MAE > 0.15 | Investigate within 1 h |
| **P2 – High** | Single held symbol failing > 5 consecutive runs; HMM always returning `None`; GARCH falling back on all tickers | Investigate same trading day |
| **P3 – Medium** | Stale account snapshot (20–48 h); dead-letter queue not empty; heartbeat > 2 h old | Investigate within 24 h |
| **P4 – Low** | Non-blocking warnings (GARCH convergence, `python-dotenv` parse warning, non-critical preflight check) | Investigate within 1 week |

---

## Active & Resolved Incidents

*(No incidents recorded yet — add entries above this line using the template.)*

---

## Maintenance Notes

- Prune entries older than 6 months to keep this file readable.
- Archive pruned entries to `docs/incident_log_archive_YYYY.md`.
- The preflight check (`python scripts/preflight_check.py`) and Gravity Audit
  (`gui/panels.py → Safety tab`) are the fastest triage tools; run them first.
- For systemic data-source failures, consult the Dependency Map
  (`gui/panels.py → Safety tab → 🕸️ Dependency Map`) to see which modules lose
  coverage before deciding on pause scope.
