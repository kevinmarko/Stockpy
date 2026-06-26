> **This checklist applies only when re-enabling broker execution
> (`ADVISORY_ONLY=false`). In advisory mode (the project default), the operational
> checklist is `docs/RUNBOOK.md §2 — Pre-Market Checklist (Daily Advisory Run)`.
> See `docs/HOW_TO_GUIDE.md → Advisory-Only Mode` for the re-enable procedure.**

# InvestYo Go-Live Checklist

> Run `python scripts/preflight_check.py` to verify all automatable items.
> Items marked *(manual)* require human sign-off before marking done.

---

## 🔐 Security

- [ ] All secrets stored in `.env` — NOT committed to git.
- [ ] `.env` is in `.gitignore`; verified with `git status --short`.
- [ ] `FRED_API_KEY` rotated within the last **90 days**.  
  Set `FRED_KEY_ROTATED_DATE=YYYY-MM-DD` in `.env` to enable automated check.
  (check wired in Stage 3 of the 2026-06-26 cleanup plan)
- [ ] `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` rotated within the last **90 days**.  
  Set `ALPACA_KEY_ROTATED_DATE=YYYY-MM-DD` in `.env` to enable automated check.
  (check wired in Stage 3 of the 2026-06-26 cleanup plan)
- [ ] *(manual)* No sensitive data (account numbers, SSN, trade history) stored unencrypted on disk.
- [ ] *(manual)* Broker account uses 2-factor authentication.

---

## ✅ Strategy Validation

- [ ] Every active strategy has a `ValidationReport.deployable == True` in `reports/`.
  - Reports must be dated within the last **30 days**.
  - Run: `python -m validation.harness --strategy <name> --start YYYY-MM-DD --end YYYY-MM-DD`
- [ ] Stress tests (Stage 3.3) passed for EVERY options-selling strategy across all four shock windows:
  - `OCT_2008` (Lehman, VIX > 80)
  - `FEB_2018` (Volmageddon / XIV blowup)
  - `MAR_2020` (COVID crash + rebound)
  - `AUG_2024` (yen carry-trade unwind)
- [ ] No strategy has `PBO >= 0.5` (overfitting risk).
- [ ] No strategy has `DSR <= 0.95`.
- [ ] No strategy has net-of-cost Sharpe ≤ 0.5.
- [ ] No strategy has Max Drawdown ≥ 30%.

---

## 📈 Paper-Trading Track Record

- [ ] Paper-traded continuously for at least **90 days**.  
  Set `PAPER_TRADING_START_DATE=YYYY-MM-DD` in `.env` to enable automated check.
- [ ] *(manual)* Paper P&L tracks backtest expectation within **±20%** over the paper-trading window.
  Document the tolerance and observed deviation in `docs/paper_trading_log.md`.
- [ ] *(manual)* No unexplained fill gaps (missed orders, incorrect quantities) during the paper period.
- [ ] Reconciliation flagged **ZERO** unexplained drifts in the last **30 days**.
  Check `output/` for any saved reconciliation reports.

---

## 🛡️ Kill Switch & Risk Gate

- [ ] Kill switch verified to halt new orders:
  ```
  python -m execution.kill_switch --activate --reason "preflight test"
  python3 main_orchestrator.py --dry-run  # should log CRITICAL + not submit
  python -m execution.kill_switch --deactivate
  ```
- [ ] Risk gate market-hours enforcement tested outside RTH:
  ```
  RISK_GATE_ENFORCE_MARKET_HOURS=true python -m scripts.preflight_check
  ```
- [ ] `FLATTEN_ON_KILL` reviewed — set to `true` if you want a reminder to flatten on kill.

---

## 🔔 Alerts & Observability

- [ ] At least one alert channel configured (`DISCORD_WEBHOOK_URL` or `SLACK_WEBHOOK_URL` or `ALERT_SMTP_HOST`).
- [ ] Test alert fires correctly:
  ```python
  from observability.alerts import send_alert
  send_alert("CRITICAL", "PREFLIGHT TEST — ignore", channels=["discord"])
  ```
- [ ] Streamlit dashboard launches without errors:
  ```
  streamlit run observability/dashboard.py
  ```
- [ ] Heartbeat file refreshes every 60 s: `ls -la output/heartbeat.txt`
- [ ] *(manual)* Watchdog process (cron / supervisor) configured to activate kill switch if heartbeat goes stale.

---

## 🗄️ Data Integrity

- [ ] SQLite backup tested:
  ```
  cp quant_platform.db quant_platform_backup_$(date +%Y%m%d).db
  # Restore test:
  sqlite3 quant_platform_backup_$(date +%Y%m%d).db "SELECT COUNT(*) FROM trades;"
  ```
  Record backup date: `DB_BACKUP_DATE=YYYY-MM-DD` in `.env` to enable automated check.
- [ ] `quant_platform.db` is included in the regular backup schedule.

---

## 💰 Capital & Sizing

- [ ] Starting capital is **small** — recommended 5–10% of intended full-size for the first month live.
- [ ] `MAX_POSITION_WEIGHT` reviewed (default 1.0 = up to 100% in one name — lower for live).
- [ ] `KELLY_FRACTION` reviewed (default 0.5 = half-Kelly — appropriate for live trading).
- [ ] *(manual)* Tax-lot tracking configured in brokerage account if applicable (US: wash-sale rules).

---

## 🚦 Final Sign-Off

- [ ] All CRITICAL checklist items above are ticked.
- [ ] `python scripts/preflight_check.py` exits with code 0.
- [ ] *(manual)* At least one human besides the primary operator has reviewed this checklist.
- [ ] Date of go-live sign-off: ___________
- [ ] Signed off by: ___________

---

> **REMEMBER**: Start small. The first month live is a calibration period, not a performance period.
> Monitor daily. Increase size only after 30+ days of unexplained-drift-free live operation.
