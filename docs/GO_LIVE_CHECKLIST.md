> **This checklist applies when re-enabling the automated Alpaca/FMP-paper broker
> execution path (`ADVISORY_ONLY=false`) — OR when going live on the separate
> Robinhood execution bridge (`ROBINHOOD_EXECUTION_MODE=live`), which is
> **independent of `ADVISORY_ONLY`** and stays gated even while `ADVISORY_ONLY=true`.
> If you are only going live on Robinhood, `ADVISORY_ONLY` should correctly stay
> `true` (it only quarantines the Alpaca surface) — do not skip this file on that
> basis; jump straight to **🤖 Robinhood Live Sign-Off** below. If neither applies
> (advisory mode, no Robinhood execution), the operational checklist is
> `docs/RUNBOOK.md §2 — Pre-Market Checklist (Daily Advisory Run)`.
> See `docs/HOW_TO_GUIDE.md → Advisory-Only Mode` for the Alpaca re-enable procedure.**

# InvestYo Go-Live Checklist

> Run `python scripts/preflight_check.py` to verify all automatable items.
> Items marked *(manual)* require human sign-off before marking done.

---

## 🔐 Security

- [ ] All secrets stored in `.env` — NOT committed to git.
- [ ] `.env` is in `.gitignore`; verified with `git status --short`.
- [ ] `FRED_API_KEY` rotated within the last **90 days**.  
  Set `FRED_KEY_ROTATED_DATE=YYYY-MM-DD` in `.env` to enable `check_key_rotation_recent`
  (preflight check #2 — warning-only, never blocking; check wired in Stage 3 of the 2026-06-26 cleanup plan).
- [ ] `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` rotated within the last **90 days**.  
  Set `ALPACA_KEY_ROTATED_DATE=YYYY-MM-DD` in `.env` to enable `check_alpaca_key_rotation_recent`
  (preflight check #3 — warning-only; auto-skipped when `ADVISORY_ONLY=true`; check wired in Stage 3 of the 2026-06-26 cleanup plan).
- [ ] *(manual)* No sensitive data (account numbers, SSN, trade history) stored unencrypted on disk.
- [ ] *(manual)* Broker account uses 2-factor authentication.
- [ ] If `ROBINHOOD_EXECUTION_MODE=live` (Tier 8 execution bridge, independent of `ADVISORY_ONLY`), `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` is set to a positive per-order dollar ceiling — `check_robinhood_execution_mode` FAILS otherwise. This check is never auto-skipped under `ADVISORY_ONLY=true` since the Robinhood path is orthogonal to the Alpaca quarantine. See **🤖 Robinhood Live Sign-Off** below for the full checklist on this path.

---

## 🤖 Robinhood Live Sign-Off

> Applies whenever `ROBINHOOD_EXECUTION_MODE=live` — regardless of `ADVISORY_ONLY`.
> This is the platform's only mechanism for placing a real Robinhood order (see
> `docs/architecture/execution.md`'s `execution/queue_builder.py` entry); it is
> entirely separate from the Alpaca/FMP-paper broker sections elsewhere in this
> checklist, which do not apply to a Robinhood-only go-live.

- [ ] Staged rollout followed in order — `off → review → live` — not jumped straight to `live`. See `docs/RUNBOOK.md`'s **Robinhood Execution Bridge (Tier 8) — paper-first rollout** section for the full staged-rollout walkthrough.
- [ ] `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` set to a positive per-order dollar ceiling (see the Security bullet above; `check_robinhood_execution_mode` in `scripts/preflight_check.py` enforces this).
- [ ] Dedicated, separately-funded Robinhood **Agentic** account opened and confirmed — the `robinhood-execution` skill refuses to operate against the main account.
- [ ] Kill switch (`python -m execution.kill_switch --status`) verified clear before the first live run.
- [ ] *(manual)* Operator has walked the full day-to-day procedure at least once in `review` mode: `docs/RUNBOOK.md`'s **Robinhood Live Execution Procedure** section.
- [ ] *(manual)* Per-trade human confirmation understood and accepted as mandatory — the `robinhood-execution` skill never batch-confirms orders.

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
  Check `settings.OUTPUT_DIR` for any saved reconciliation reports — this defaults to
  `<LOCAL_DATA_ROOT>/output` (i.e. `~/.stockpy_local/output/`), not a repo-relative `output/`,
  as of `settings.LOCAL_DATA_ROOT` (2026-08).

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
- [ ] Command Center launches without errors and the Observability tab renders:
  ```
  streamlit run gui/app.py
  ```
- [ ] Heartbeat file refreshes every 60 s: `ls -la ~/.stockpy_local/output/heartbeat.txt`
  (or `$LOCAL_DATA_ROOT/output/heartbeat.txt` if you've overridden the default — not a
  repo-relative `output/`, as of `settings.LOCAL_DATA_ROOT` — 2026-08).
- [ ] *(manual)* Watchdog process (cron / supervisor) configured to activate kill switch if heartbeat goes stale.

---

## 🗄️ Data Integrity

- [ ] SQLite backup tested — as of `settings.LOCAL_DATA_ROOT` (2026-08), `quant_platform.db`
  lives under `$LOCAL_DATA_ROOT` (default `~/.stockpy_local/`), OUTSIDE the git checkout, not
  at the repo root:
  ```
  cp ~/.stockpy_local/quant_platform.db ~/.stockpy_local/quant_platform_backup_$(date +%Y%m%d).db
  # Restore test:
  sqlite3 ~/.stockpy_local/quant_platform_backup_$(date +%Y%m%d).db "SELECT COUNT(*) FROM trades;"
  ```
  Substitute your actual `LOCAL_DATA_ROOT` if you've overridden it from the default — see
  `docs/architecture/data-layer.md`'s `settings.LOCAL_DATA_ROOT` subsection for the full layout.
  Record backup date: `DB_BACKUP_DATE=YYYY-MM-DD` in `.env` to enable automated check.
- [ ] `quant_platform.db` (under `$LOCAL_DATA_ROOT`) is included in the regular backup schedule.

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
