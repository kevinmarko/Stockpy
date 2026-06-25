# InvestYo Go-Live Runbook

Operational reference for the first 5 trading days live and ongoing incident response.

---

## 0. Everyday Startup (macOS double-click)

The fastest way to start the platform is to **double-click `launch.command`** in Finder or the Dock. The script:

1. Verifies `.venv` exists and Python is exactly 3.12.x before starting.
2. Prints a clear error (and pauses for you to read it) if either check fails.
3. Runs `python main.py --interval 60` by default — refreshes every 60 s until you close the window.
4. Pauses with "Press any key to close" after exit so final output is always visible.

**To change the interval**: open `launch.command` in any text editor, set `REFRESH_INTERVAL_SECONDS=N` at the top (0 = single run).

**If `.venv` is missing** (e.g., fresh clone):

```bash
cd /Users/kevinlee/Desktop/Stockpy
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Then double-click `launch.command` again.

**If the wrong Python version is detected**: the launcher tells you which version was found and how to recreate `.venv` with Python 3.12.

---

## 1. Switching from Paper to Live

### Pre-switch (T-1 day)

1. Complete every item in `docs/GO_LIVE_CHECKLIST.md`.
2. Run `python scripts/preflight_check.py` — must exit **0**.
3. Notify all stakeholders that live trading begins the next session.
4. Ensure the kill switch is **INACTIVE**: `python -m execution.kill_switch --status`

### Day-of switch (pre-market, ≥ 30 min before open)

1. Rotate `.env` value: **`ALPACA_PAPER=false`**
2. Verify the new value is loaded:
   ```python
   from settings import settings
   assert settings.ALPACA_PAPER is False
   ```
3. Start the orchestrator in **dry-run** once to confirm it reads the live endpoint:
   ```
   python3 main_orchestrator.py --dry-run
   ```
   Look for: `"AlpacaBroker initialized — paper=False"` in the logs (not `paper=True`).
4. Remove `--dry-run` for the first live run:
   ```
   python3 main_orchestrator.py
   ```
5. Confirm in Alpaca dashboard that the account shows the same open positions as `transactions_store`.

---

## 2. Pre-Market Checklist (First 5 Live Days)

Run this EVERY trading morning before 09:00 ET:

| Check | Command / Action |
|-------|-----------------|
| **Start pipeline** | Double-click `launch.command` (auto-interval mode, 60 s refresh) |
| Kill switch inactive | `python -m execution.kill_switch --status` |
| Heartbeat recent | `ls -la output/heartbeat.txt` (< 2 h old) |
| Preflight pass | `python scripts/preflight_check.py` (exit 0) |
| Dashboard open | `streamlit run observability/dashboard.py` |
| No reconciliation drift | Dashboard → Open Positions panel |
| Risk gate log clean | Dashboard → Risk Gate Block Log (no unexpected blocks) |
| VIX check | Dashboard → VIX panel (> 25 → consider extra caution) |
| Macro regime | Dashboard → Macro Regime (RISK ON = proceed) |

---

## 3. Incident Response

### 3.1 Reconciliation Drift

**Symptom**: `CRITICAL` log: `"RECONCILIATION DRIFT"`. Alert webhook fires.

**Immediate action**:
1. Activate kill switch to halt new orders:
   ```
   python -m execution.kill_switch --activate --reason "reconciliation drift"
   ```
2. Log into Alpaca dashboard and compare positions manually.
3. Update `transactions_store` if the broker is the source of truth (rare edge case:
   a fill that arrived while the orchestrator was down).
4. Fix the discrepancy, then deactivate the kill switch:
   ```
   python -m execution.kill_switch --deactivate
   ```

**Root causes to check**:
- Order submitted but fill arrived during a process restart.
- Alpaca position expired (options) without a corresponding close in the store.
- Network partition caused a partial fill that wasn't recorded.

---

### 3.2 Kill Switch Fails to Block

**Symptom**: `KILL_SWITCH` file exists but orders are still submitted.

**Immediate action**:
1. Stop the orchestrator process immediately (`Ctrl+C` or `kill <PID>`).
2. Flatten all positions manually via Alpaca dashboard.
3. **Do not restart** until root cause is found.

**Debug**:
- Confirm the sentinel file path: `ls -la output/KILL_SWITCH`
- Confirm `settings.OUTPUT_DIR` matches where the orchestrator writes: `python -c "from settings import settings; print(settings.OUTPUT_DIR)"`
- Confirm `OrderManager` is using `GlobalKillSwitch()` (not a patched/injected instance).

---

### 3.3 Broker Connection Lost

**Symptom**: `AlpacaBroker` raises `RuntimeError` or connection timeout; 
`_execute_broker_orders` logs `ERROR`.

**Immediate action**:
1. Check Alpaca status page: https://status.alpaca.markets
2. If planned maintenance: activate kill switch until connectivity restored.
3. If unexpected: check for API key rotation requirement.

**Recovery**:
- Reconnect is automatic on the next orchestrator run (Alpaca SDK reconnects).
- Run reconciliation manually after reconnect to confirm state is consistent.

---

### 3.4 Validation Report Missing for Active Strategy

**Symptom**: Dashboard shows "No validation reports" OR `preflight_check.py` fails `check_validation_reports`.

**Immediate action**:
1. Do NOT deploy the strategy live until a fresh report is generated.
2. Run the harness:
   ```
   python -m validation.harness --strategy <name> --start 2015-01-01 --end 2024-12-31
   ```
3. If the strategy fails validation, deactivate it (remove from `settings.DEFAULT_TICKERS` or reduce weight to 0 in `settings.SIGNAL_WEIGHTS`).

---

### 3.5 Portfolio Heat Exceeding Limit

**Symptom**: Risk gate blocks new BUY orders with `"portfolio_heat"` reason; 
Dashboard shows heat > 6%.

**Normal response**: This is the risk gate working correctly. Do not override it.
- Review open positions to understand the source of adverse P&L.
- Consider tightening stop losses.
- The gate unblocks automatically once heat drops below `settings.MAX_PORTFOLIO_HEAT`.

---

### 3.6 HMM Says High Risk-Off

**Symptom**: Risk gate blocks BUY orders with `"hmm_regime"` reason;
HMM risk-on probability < `1 - HMM_RISK_OFF_BLOCK_THRESHOLD` (default 0.80).

**Normal response**: This is also the risk gate working correctly.
- SELL signals are never blocked by the HMM gate.
- The gate clears automatically as the HMM model updates in subsequent pipeline runs.
- Do not override unless you have high conviction the HMM is wrong AND you have
  documented reasoning.

---

## 4. Contacts

| Role | Contact | Notes |
|------|---------|-------|
| Alpaca broker support | support@alpaca.markets | For fill disputes, account issues |
| Alpaca status | https://status.alpaca.markets | Outages / maintenance windows |
| FRED API issues | https://fred.stlouisfed.org/docs/api/ | Key rotation, rate limits |

---

## 5. Regular Maintenance

| Frequency | Task |
|-----------|------|
| Daily | Review dashboard, check reconciliation |
| Weekly | Review risk gate block log; investigate any `minimum_validation` blocks |
| Monthly | Rotate API keys; re-run validation harness for all strategies |
| Quarterly | Full review of `MAX_POSITION_WEIGHT`, `KELLY_FRACTION`, `KELLY_CAP`; update capital sizing |
| Annually | Full stress-test re-run for options-selling strategies |

---

## 6. Emergency Shutdown Procedure

If something catastrophic happens (market crash, broker API breach, runaway orders):

```bash
# 1. Immediately halt all new orders
python -m execution.kill_switch --activate --reason "EMERGENCY SHUTDOWN"

# 2. Stop the orchestrator
pkill -f main_orchestrator.py  # or kill the process by PID

# 3. Manually flatten all positions via Alpaca web dashboard
# https://app.alpaca.markets/paper/dashboard/overview

# 4. Back up the database
cp quant_platform.db quant_platform_emergency_$(date +%Y%m%d_%H%M%S).db

# 5. Document the incident in docs/incident_log.md
```

Do NOT restart the orchestrator until:
- Root cause is identified.
- All positions are either closed or reconciled.
- `preflight_check.py` exits 0.
