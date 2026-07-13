# Observability Plan — Alert-System Consolidation & Coverage (Gemini-facing)

> **This document is self-contained.** It is handed to a separate AI agent
> (Gemini) who starts cold with no prior exploration of this codebase.
> Everything needed to execute is embedded below, including verbatim
> current-state API references and line numbers. You should not need to go
> spelunking through the source to begin — though you should of course read
> the actual files before editing them, since line numbers drift.

---

## ⚠️ MANDATORY SEQUENCING GATE — READ THIS FIRST

This platform is **mid-flight on an 8-agent parallel hardening effort**
touching `execution/` and `gui/` (spawned 2026-07-13, still in progress at
the time this plan was written). **Two of those in-flight agents touch the
exact file this plan's Phase O1/O2 need to edit — `observability/alerts.py`:**

- **E2** (branch `exec-unified-alerting`) — may add a small additive helper
  to `observability/alerts.py`, and wires the currently-orphaned
  `send_daily_summary()` into `main_orchestrator.py`'s end-of-cycle.
- **E4** (branch `exec-fill-stream-flatten`) — **calls** (does not edit)
  `observability.alerts.send_alert` from a new hardened fill-stream consumer
  and a new gated dry-run flatten-on-kill proposal module.

**As of this plan being written, neither branch has merged to `main`** —
verified via:
```bash
gh pr list --state merged --limit 30      # neither exec-unified-alerting nor
                                            # exec-fill-stream-flatten appear
git log origin/main --oneline --grep="alerting" -i
git log origin/main --oneline --grep="flatten" -i
git branch -r | grep -E "exec-unified-alerting|exec-fill-stream-flatten"
                                            # neither branch is even pushed to
                                            # origin yet — they exist only as
                                            # local worktrees at the time of writing
```

**You MUST re-run these checks yourself before touching
`observability/alerts.py`.** If E2 and/or E4 have not both merged to `main`
by the time you start:

1. **Do not begin editing `observability/alerts.py`.** A concurrent edit to
   the same file across two independent agents is a real merge-conflict /
   silent-clobber risk, not a formality — E2 is adding code to the exact
   function bodies (`send_alert`, plus a new helper) this plan's Phase O2
   also needs to touch.
2. Either **wait** (poll `gh pr list --state merged` periodically) or
   **explicitly raise it with the user** before proceeding out of sequence.
3. You MAY start immediately on any phase that does NOT touch
   `observability/alerts.py` — e.g. Phase O0 (root `alerting.py`
   consolidation research) is read/plan-only and phases scoped to
   `execution/kill_switch.py`, `execution/risk_gate.py`, or
   `validation/drift.py` call-site wiring do not conflict with E2/E4's
   in-flight diffs (double-check via `git diff origin/main...<their-branch>
   -- <file>` if unsure before editing any file the audit below names).

Once both branches are confirmed merged, proceed with the full phase list in
section (e) below, including Phase O2 (`observability/alerts.py` itself).

---

## (a) Context & goal

**Goal:** this platform has **two parallel, independently-evolved alerting
systems** plus a growing set of call sites that *should* fire alerts but
don't. The goal of this plan is threefold:

1. **Clarify (not necessarily merge) the two systems' roles** so a future
   contributor doesn't have to reverse-engineer which one to use for a new
   alert.
2. **Wire the alert triggers this platform already documents as existing**
   but that a live grep shows are never actually fired.
3. **Close the two structural gaps** every alerting system needs and this
   one currently has none of: **dedup/rate-limiting** (to prevent an alert
   storm — e.g. a stuck reconciliation-drift condition firing on every
   symbol, every cycle) and a **channel health-check / self-test** (so an
   operator can verify Discord/Slack/email are actually reachable before
   relying on them during a real incident).

**Why this matters operationally:** `docs/RUNBOOK.md` §3 walks an operator
through incident playbooks assuming alerts already fired. If the underlying
trigger (e.g. kill-switch activation) never actually calls `send_alert`, the
operator's first signal of an incident is discovering it manually — the
exact failure mode structured alerting exists to prevent.

**Scope boundary (from CLAUDE.md's "Observability / Mission Control"
paragraph):** `gui/panels/observability.py` (the GUI's Observability tab) is
**OUT OF SCOPE** for this plan — it is owned by a separate in-flight agent
(`gui-observability-helpers`) in the current 8-agent effort. This plan is
about the alert **dispatch/trigger** layer (`observability/alerts.py`, root
`alerting.py`, and the call sites that should invoke them), not the GUI
panel that reads `output/state_snapshot.json` for display.

---

## (b) Two-agent boundary

You (Gemini) own **`observability/`** (specifically `observability/alerts.py`,
gated per the sequencing rule above) **and root-level `alerting.py`** in this
phase. Claude does **NOT** touch these files in this phase — Claude's
concurrent work is elsewhere (see `docs/CONFIG_SCHEMA_PLAN.md` for Claude's
sibling assignment, which has zero file overlap with this one).

**You own (and may edit, subject to the sequencing gate above):**
- `observability/alerts.py` — channel dispatch, dedup/rate-limiting,
  health-check additions.
- `alerting.py` (root) — ntfy.sh push notifications; only touch if your
  consolidation phase (O0) concludes a change here is warranted.
- New call-site wiring in `execution/kill_switch.py`,
  `execution/risk_gate.py`, `execution/order_manager.py` (the alert-dispatch
  line only — do NOT touch its broker/reconciliation logic),
  `validation/drift.py` (already wired — read-only unless a bug surfaces).
- Their tests: `tests/test_alerts.py`, `tests/test_alerting.py`, plus new
  tests for any newly-wired call site (e.g. `tests/test_kill_switch.py` gains
  an alert-dispatch assertion).

**You must NOT edit:** `config.py`, `database_setup.py`,
`gui/panels/observability.py` (owned elsewhere), or any file under
`execution/` beyond the specific alert-dispatch call sites named above (do
not refactor `PreTradeRiskGate`'s check logic itself, only add an alert call
after a check result is already computed).

**Note on `execution/order_manager.py`'s own `_send_alert`:** this is a
**pre-existing, separate, ad-hoc webhook poster** (see section (c)) that
predates `observability/alerts.py`. Phase O1 asks you to characterize and
propose (not silently auto-merge) a consolidation — see that phase for the
constraint on preserving `ReconciliationReport`'s existing behavior.

---

## (c) Current-state API map (verbatim — so you need no exploration)

### System 1 — `observability/alerts.py` (391 lines)

The general-purpose, multi-channel alert dispatcher.

- **`AlertLevel`** — `Literal["INFO", "WARNING", "CRITICAL"]` — `:73`.
- **`ALL_CHANNELS`** — `("console", "file", "discord", "slack", "email")` — `:85`.
- **`_active_channels() -> list[str]`** — `:92`. Evaluated at dispatch time
  (not import time). `console` always active. `file` active iff
  `settings.ALERT_FILE_PATH` set. `discord`/`slack` active iff their webhook
  URL settings are set. `email` active iff ALL of `ALERT_SMTP_HOST`,
  `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO` are set (partial config silently
  ignored, not an error).
- **`send_alert(level, message, channels=None, extra=None) -> None`** —
  `:121`. Dispatches to every active channel (or an explicit subset).
  **Never raises** — every channel write is wrapped in `except Exception` at
  `:185-191`, logged at `logger.error`, discarded. This broad-catch is
  explicitly documented as load-bearing in the module docstring (`:14-21`).
- **`send_daily_summary(pnl_summary, warnings) -> None`** — `:194`. Composes
  a Markdown-ish multi-line INFO alert (P&L by strategy + warnings list) and
  calls `send_alert("INFO", message, extra={"type": "daily_summary", ...})`.
  **Currently called from nowhere in production code** — only
  `tests/test_alerts.py:435,457` call it. This is the exact gap E2
  (`exec-unified-alerting`, in-flight, not yet merged as of this writing) is
  wiring into `main_orchestrator.py`'s end-of-cycle — **do not duplicate that
  wiring yourself; wait for it to land** (see the sequencing gate above).
- **Channel implementations** — `_send_console` `:250`, `_send_file` `:266`
  (JSON-lines append to `settings.ALERT_FILE_PATH`), `_send_discord` `:287`
  (`{"content": "<emoji> **[LEVEL]** \`ts\`\nmessage"}`), `_send_slack`
  `:318` (`{"text": "<emoji> *[LEVEL]* \`ts\`\nmessage"}`), `_send_email`
  `:346` (STARTTLS on `ALERT_SMTP_PORT`, default 587).
- **Documented alert-level contract (module docstring, `:46-51`)** —
  this is the list you're auditing against in Phase O3:
  ```
  CRITICAL — kill switch activated, reconciliation drift detected, broker
             connection lost, missing/non-deployable validation report.
  WARNING  — portfolio heat approaching limit (>5%), single-name correlation
             concentration, large fill slippage versus the expected model cost.
  INFO     — order filled, daily rebalance complete, daily summary.
  ```

### System 2 — root `alerting.py` (316 lines)

A **separate, older, narrower** system — ntfy.sh push notifications plus
root-logger setup. Docstring at `:1-42` explicitly frames it as the
`main.py`-integration module.

- **`setup_logging(log_level="INFO") -> None`** — `:73`. Idempotent
  (`if root.handlers: return` guard). `RotatingFileHandler` →
  `logs/investyo.log` (10 MB × 5 backups) + `StreamHandler` to stderr.
- **`notify(title, message, priority="default") -> None`** — `:146`. POSTs
  to `https://ntfy.sh/{NTFY_TOPIC}` via stdlib `urllib.request`. No-op when
  `NTFY_TOPIC` env var (read via `os.environ`, **not** `settings`) is unset.
  Never raises (`except urllib.error.URLError` + broad `except Exception`,
  both → `logger.warning`).
- **`summarize_run(result) -> str`** — `:226`. Duck-typed on `RunResult`
  (avoids circular import) — produces a multi-line text summary (symbol
  counts, BUY/HOLD/SELL tally, error count, top-3 actionable recs by
  conviction).
- **Callers (confirmed via grep — only `main.py` uses this module):**
  ```
  main.py:1050:  notify(...)   # after result.errors is non-empty → high priority
  main.py:1062:  notify(...)   # first clean run of an --interval session → default priority
  ```
  `setup_logging()` is main.py's first call inside `main()`.
  `summarize_run()` is logged at INFO after every `run_once()`.

**The two systems do not call each other and share no code.** They are
genuinely parallel: `observability/alerts.py` is the multi-channel
(console/file/Discord/Slack/email) system used by
`prompt_registry`/`validation/drift`/tests; root `alerting.py` is the
ntfy.sh-only system used exclusively by `main.py`'s advisory loop. Neither
docstring cross-references the other.

### `observability/dashboard.py` — CONFIRMED RETIRED

```bash
$ ls observability/
__init__.py  alerts.py
```
Does not exist. Per CLAUDE.md: "The standalone Streamlit paper-trading
observability dashboard has been removed; its panels are folded into the
Command Center's Observability tab (`gui/panels/observability.py`)." That GUI
panel is out of scope here (see section (a)).

### Audit — documented triggers vs. actual firing call sites (grep-verified)

This is the load-bearing finding of this plan. Cross-referenced against
`execution/order_manager.py`, `execution/kill_switch.py`,
`execution/risk_gate.py`, `validation/drift.py`, `prompt_registry/*.py`,
`scripts/preflight_check.py`:

| Documented trigger (alerts.py `:46-51`) | Wired to `observability.alerts.send_alert`? | Evidence |
|---|---|---|
| CRITICAL: kill switch activated | ❌ **NOT WIRED** | `execution/kill_switch.py::GlobalKillSwitch.activate()` (`:75`) only calls `logger.critical(...)` at `:87`/`:94`. No `send_alert`/`notify` import or call anywhere in the file. |
| CRITICAL: reconciliation drift detected | ⚠️ **WIRED TO A DIFFERENT, AD-HOC SYSTEM** | `execution/order_manager.py::OrderManager._send_alert()` (`:402`) — its own private method — POSTs directly via `urllib.request` to `self._alert_url` (`:181`, sourced from `settings.ALERT_WEBHOOK_URL`, a **third**, separate env var from `DISCORD_WEBHOOK_URL`/`SLACK_WEBHOOK_URL`). Called from `reconcile_state()` at `:347` when `report.has_drift`. This never touches `observability.alerts.send_alert` — it bypasses the file/console/email channels and the dedup work in Phase O2 entirely. |
| CRITICAL: broker connection lost | ❌ **NOT WIRED — no implementation found at all** | No `send_alert`/`notify` call site references broker-connection-lost anywhere in `execution/`. |
| CRITICAL: missing/non-deployable validation report | ❌ **NOT WIRED** | `scripts/preflight_check.py::check_validation_reports()` (`:933`) only returns a `CheckResult` for the CLI/JSON output — no alert dispatch. |
| WARNING: portfolio heat >5% | ❌ **NOT WIRED** | `execution/risk_gate.py::PreTradeRiskGate.portfolio_heat_check()` (`:193`) returns a pass/fail `CheckResult`-style verdict consumed by `run_all()`'s short-circuit; no alert call. |
| WARNING: correlation concentration | ❌ **NOT WIRED** | `execution/risk_gate.py::PreTradeRiskGate.max_correlation_check()` (`:216`) — same pattern, no alert call. |
| WARNING: large fill slippage vs. model cost | ❌ **NOT WIRED** | No call site found. |
| INFO: order filled | ❌ **NOT WIRED** | No call site found. |
| INFO: daily rebalance complete | ❌ **NOT WIRED** | No call site found. |
| INFO: daily summary (`send_daily_summary`) | ❌ **ORPHANED (in-flight fix, do not duplicate)** | Called from nowhere in production code; E2 (in-flight, see gate above) is wiring this into `main_orchestrator.py`. |

**Two triggers ARE genuinely wired today:**

1. **CRITICAL — Prompt Registry rejection** (not in the original docstring
   list, but a real, working CRITICAL alert):
   `prompt_registry/registry.py::PromptRegistry._reject()` (`:375`) lazily
   imports `observability.alerts.send_alert` and calls it at `:397` with
   `level="CRITICAL"`. Wrapped in its own `try/except` → `logger.debug` on
   failure (`:398-399`), per CONSTRAINT #6.
2. **WARNING — Calibration/regime drift**:
   `validation/drift.py::check_and_alert_recommendation_drift()` (`:491`)
   lazily imports and calls `send_alert("WARNING", ...)` when
   `result.drift_detected`, with a `send_alert_fn` injection param for
   tests. Its only current caller is
   `scripts/preflight_check.py::check_calibration_drift()` (`:1041`) — a
   **non-blocking preflight CLI check**, not a live pipeline hook. So this
   alert only fires when an operator manually runs
   `python scripts/preflight_check.py` — never automatically during a live
   advisory/orchestrator cycle.

### Dedup / rate-limiting — CONFIRMED ABSENT

```bash
$ grep -n "dedup\|rate.limit\|throttle\|cooldown\|_alert_history\|last_alert" \
    observability/alerts.py alerting.py
(no matches)
```
`send_alert()` has zero memory of prior calls. A condition that re-evaluates
to true every cycle (e.g. sustained portfolio heat, if wired per Phase O3)
would fire an identical alert on every single invocation with no
suppression window — the classic alert-storm failure mode this plan's Phase
O2 addresses.

### Channel health-check / self-test — CONFIRMED ABSENT

No function in `observability/alerts.py` or `alerting.py` proactively probes
channel reachability (e.g. a lightweight test POST to the configured Discord
webhook). An operator currently only discovers a broken webhook when a real
alert silently fails and logs `logger.error` — which is easy to miss in
day-to-day log volume.

### Existing test coverage

- **`tests/test_alerts.py`** (474 lines) — covers `observability/alerts.py`
  per-channel behavior (`TestConsoleChannel`, `TestFileChannel`,
  `TestDiscordChannel`, `TestSlackChannel`, `TestEmailChannel`,
  `TestDailySummary`), plus `test_unconfigured_channel_skipped`. **No dedup
  or rate-limit tests exist** (nothing to test yet).
- **`tests/test_alerting.py`** — covers root `alerting.py`'s
  `setup_logging`/`notify`/`summarize_run` in isolation (careful
  save/restore of the real root logger's handlers, per its own docstring).
- **`tests/test_drift.py:288`** — `test_no_drift_does_not_call_send_alert`
  confirms the drift-alert wiring's negative case.
- **No test exercises `execution/kill_switch.py`, `execution/risk_gate.py`,
  or `execution/order_manager.py`'s reconciliation path asserting a call
  into `observability.alerts.send_alert`** — consistent with the audit
  finding above that none of them call it yet.

### Alert-related settings (`settings.py`, all correctly classified as
`SECRET_KEYS` in `gui/env_io.py:148-183` — never GUI-writable, confirmed, no
gap here)

```
ALERT_FILE_PATH        settings.py:360   (not in SECRET_KEYS OR ALLOWED_KEYS
                                           in gui/env_io.py — currently
                                           un-settable from the GUI at all;
                                           minor, non-blocking finding, see
                                           Phase O2's optional note)
DISCORD_WEBHOOK_URL     settings.py:352   SECRET_KEYS
SLACK_WEBHOOK_URL       settings.py:356   SECRET_KEYS
ALERT_EMAIL_FROM/TO     settings.py:364-368  SECRET_KEYS
ALERT_SMTP_HOST/PORT/
  USER/PASSWORD         settings.py:369-372  SECRET_KEYS (PORT is the only
                                              non-secret int default=587)
ALERT_WEBHOOK_URL       settings.py:306   SECRET_KEYS — order_manager.py's
                                           OWN, separate webhook target (see
                                           Phase O1)
NTFY_TOPIC              (root alerting.py, read via os.environ)  SECRET_KEYS
```

---

## (d) MCP verification notes

Use the InvestYo MCP tools to verify against live-ish platform state:

- **`mcp__investyo__read_platform_logs`** — after wiring a new alert trigger
  (e.g. kill-switch activation), trigger it in a controlled test and confirm
  the `logger.error`/`logger.critical` line appears with the expected
  channel-dispatch context.
- **`mcp__investyo__run_platform_tests`** — full suite must stay green;
  run this after every phase, not just at the end.
- **`mcp__investyo__query_investyo_db`** — if Phase O2's dedup mechanism
  persists any state (e.g. a last-fired timestamp table), inspect it
  directly to confirm dedup windows are respected.
- **`mcp__investyo__trigger_data_engine`** — not directly relevant to this
  plan, but useful if a phase needs a live cycle to exercise a newly-wired
  trigger end-to-end rather than only via unit test.

For library API questions (e.g. `smtplib`, `email.mime`) use the `context7`
docs tools. Discord/Slack webhook payload shapes are already fully
documented in section (c) above — no external lookup needed for those.

---

## (e) Phases O0–O4

### O0 — Two-system consolidation research + decision doc (no code changes)

**Problem.** Two independently-evolved alerting systems
(`observability/alerts.py` multi-channel, root `alerting.py` ntfy-only) plus
a **third**, ad-hoc webhook poster inside `execution/order_manager.py`
(`_send_alert`, `:402`) exist with no cross-reference and genuinely
different env-var namespaces (`DISCORD_WEBHOOK_URL`/`SLACK_WEBHOOK_URL` vs.
`ALERT_WEBHOOK_URL` vs. `NTFY_TOPIC`). A future contributor adding a new
alert has no clear "use this one" signal.

**Change.** Write a short decision note (as a docstring addition at the top
of `observability/alerts.py`, cross-referencing root `alerting.py`, plus a
matching note at the top of root `alerting.py` cross-referencing
`observability/alerts.py`) stating each system's actual scope:
- `observability/alerts.py` = the general multi-channel system for
  strategy/risk/execution-layer alerts (CRITICAL/WARNING/INFO going to
  Discord/Slack/email/file/console).
- root `alerting.py` = specifically the `main.py` advisory-loop's mobile
  push (ntfy.sh) + root logging setup — a narrower, mobile-notification-
  specific concern that is NOT a candidate for merging into
  `observability/alerts.py` (different channel entirely, different
  audience — a phone push vs. an ops channel).
- **Do NOT merge the two modules.** They serve genuinely different purposes
  (root `alerting.py`'s `notify()` is a personal mobile-push mechanism tied
  to one operator's phone; `observability/alerts.py` is a
  team/ops-channel dispatcher). Consolidation here means *documentation
  clarity*, not code merging.
- Recommend (but do not silently implement without re-reading Phase O1)
  that `execution/order_manager.py`'s ad-hoc `_send_alert`/`ALERT_WEBHOOK_URL`
  be migrated onto `observability.alerts.send_alert` — see Phase O1.

**Files.** `observability/alerts.py` (docstring only), `alerting.py`
(docstring only).

**Verify.** No behavior change — `pytest tests/test_alerts.py
tests/test_alerting.py` stays green untouched.

### O1 — Migrate `OrderManager._send_alert` onto `observability.alerts.send_alert`

**Problem.** `execution/order_manager.py::_send_alert()` (`:402`)
re-implements a webhook POST from scratch (`:404-424`) rather than reusing
`observability.alerts.send_alert`. This means: (a) reconciliation-drift
CRITICAL alerts bypass the `file`/`console`/`email` channels entirely — they
ONLY reach whatever `ALERT_WEBHOOK_URL` points at; (b) it duplicates the
Discord/Slack payload-shaping logic `observability/alerts.py` already has;
(c) it does not benefit from Phase O2's dedup/rate-limiting once that
lands.

**Change.** Replace the body of `_send_alert()` with a call to
`observability.alerts.send_alert("CRITICAL", <same formatted message>,
extra={...structured drift fields...})`, using a **lazy import** (matching
the `prompt_registry/registry.py:391` and `validation/drift.py:539`
convention — this file must not hard-depend on `observability` at module
load time). **Preserve `ReconciliationReport`'s existing return contract and
`reconcile_state()`'s control flow exactly** — this is purely swapping the
alert-dispatch implementation, not touching drift-detection logic.
Deprecate (but do not delete outright without confirming no other reader
depends on it — grep first) the standalone `ALERT_WEBHOOK_URL` setting in
favor of routing through the `DISCORD_WEBHOOK_URL`/`SLACK_WEBHOOK_URL`
channels already active in `_active_channels()`; if `ALERT_WEBHOOK_URL` is
still set and neither of those is, keep a fallback path that posts to it
directly so an operator who has only configured `ALERT_WEBHOOK_URL` doesn't
silently lose their alert on upgrade.

**Files.** `execution/order_manager.py`, `tests/test_reconciliation.py` (new
assertion that drift calls `observability.alerts.send_alert` with
`level="CRITICAL"`, mirroring `tests/test_drift.py`'s
`send_alert_fn` injection pattern).

**Verify.** `tests/test_reconciliation.py` and `tests/test_order_manager_idempotency.py`
stay green; new test confirms `send_alert` is called (mock/injection, no
real network) on drift, not called on clean state.

### O2 — Dedup / rate-limiting (**gated — see the sequencing gate above**)

**Problem.** `send_alert()` has no memory of prior calls (confirmed absent
in section (c)). Any newly-wired trigger from Phase O3 that re-evaluates
every cycle (e.g. sustained portfolio heat) would otherwise fire an
identical alert every cycle — an alert storm that trains operators to
ignore the channel.

**Change.** Add an in-process, TTL-based dedup layer to
`observability/alerts.py`:
- A new optional `dedup_key: Optional[str] = None` parameter on
  `send_alert()`. When provided, the alert is suppressed (logged at DEBUG,
  not dispatched to any channel) if an alert with the same `dedup_key` was
  sent within `settings.ALERT_DEDUP_WINDOW_SECONDS` (new setting, default a
  reasonable value such as 900 = 15 minutes — pick a value and justify it in
  the docstring; do not leave it unconfigured).
- Store the dedup state as a simple in-process
  `dict[str, float]` (`dedup_key -> last_sent_monotonic_ts`), matching this
  codebase's existing in-process-cache convention (see
  `data/market_data.py`'s `_BarsCache`/`_FundamentalsCache` for the pattern
  — TTL via `time.monotonic()`, never persisted to disk). This is
  intentionally NOT a DB table — alert dedup is a per-process, best-effort
  concern, not a durable audit trail (the `file` channel already provides
  the durable JSONL audit trail).
- `channels=None` (the default) behavior is unaffected when `dedup_key` is
  omitted — this must be purely additive; every existing caller (Phase O0's
  audit, `prompt_registry`, `validation/drift.py`) keeps working unchanged
  with no `dedup_key`.
- Add a `reset_dedup_state()` module-level function for test isolation
  (mirroring `data/market_data.py::reset_provider()`'s pattern).

**Files.** `observability/alerts.py`, `settings.py` (new
`ALERT_DEDUP_WINDOW_SECONDS` setting), `tests/test_alerts.py` (new
`TestDedup` class: same `dedup_key` within window suppressed, different key
not suppressed, same key after window elapses fires again, no `dedup_key`
== always fires).

**Verify.** New dedup tests green; all pre-existing `tests/test_alerts.py`
tests (which never pass `dedup_key`) remain green unchanged.

### O3 — Wire the documented-but-unfired triggers

**Problem.** Section (c)'s audit table shows 7 of the 9 documented triggers
in `alerts.py`'s own docstring are not wired to fire at all, and one more
(reconciliation drift) fires through a bypass system Phase O1 fixes.

**Change.** Wire the highest-value, lowest-risk subset first (do not attempt
all 7 in one PR — this is a multi-PR phase; prioritize by blast radius):
1. **Kill switch activation** (`execution/kill_switch.py::activate()`,
   `:75`) — add a `send_alert("CRITICAL", f"Kill switch activated: {reason}",
   dedup_key="kill_switch_activate")` call alongside the existing
   `logger.critical` at `:87`. This is the single highest-value wiring in
   this phase — a silent kill-switch activation is the platform's worst
   observability gap.
2. **Missing/non-deployable validation report** — add an alert call inside
   `scripts/preflight_check.py::check_validation_reports()` (`:933`) when
   `problems` is non-empty, gated so it only fires from a scheduled/CI
   invocation of preflight, not on every ad-hoc manual run (use a
   `fire_alert: bool = False` parameter defaulting off, matching
   `validation/drift.py`'s `send_alert_fn` injection style for testability).
3. **Portfolio heat / correlation concentration WARNINGs** —
   `execution/risk_gate.py`'s `portfolio_heat_check()` (`:193`) and
   `max_correlation_check()` (`:216`) already compute a pass/fail verdict;
   add a `send_alert("WARNING", ..., dedup_key=f"heat_{symbol}")` /
   `dedup_key="correlation_concentration"` call on the failing branch only
   (never on pass — that would be an INFO-level alert storm, not a warning).

Leave **broker connection lost**, **large fill slippage**, **order filled**,
and **daily rebalance complete** as explicitly documented future work (list
them in this phase's PR description) rather than half-wiring them without a
clear source event — do not fabricate a plausible-looking call site if the
actual triggering condition isn't cleanly available at that point in the
code (e.g. "broker connection lost" has no single owning module today; do
not force a workaround).

**Files.** `execution/kill_switch.py`, `scripts/preflight_check.py`,
`execution/risk_gate.py`, plus each one's corresponding test file
(`tests/test_kill_switch.py`, `tests/test_preflight.py`,
`tests/test_risk_gate.py` / `tests/test_correlation_check.py`).

**Verify.** New assertions in each test file that the alert fires on the
failing condition and does not fire on the passing condition; full suite
green.

### O4 — Channel health-check / self-test

**Problem.** No function proactively verifies a configured channel is
reachable; a broken webhook is discovered only when a real alert silently
fails.

**Change.** Add `check_channel_health() -> dict[str, dict]` to
`observability/alerts.py` — for each active channel (per
`_active_channels()`), attempt a lightweight, clearly-labeled test dispatch
(e.g. `send_alert("INFO", "[Health Check] observability/alerts.py self-test
— ignore", channels=[ch])`) and report `{channel: {"ok": bool, "error":
Optional[str]}}`. Wire this into `scripts/preflight_check.py` as a new,
**warning-only** check (`check_alert_channels_reachable`) — never blocking,
since a broken Discord webhook must not gate deployment.

**Files.** `observability/alerts.py`, `scripts/preflight_check.py`,
`tests/test_alerts.py` (new `TestChannelHealth` class — mocked network,
never a real webhook POST in tests), `tests/test_preflight.py`.

**Verify.** New tests confirm a channel reporting failure doesn't raise and
the preflight check is warning-only (never fails the overall gate).

---

## (f) Done-definition

You are done when **all** of the following hold:

1. **The sequencing gate is honored** — every edit to
   `observability/alerts.py` happened only after confirming both E2
   (`exec-unified-alerting`) and E4 (`exec-fill-stream-flatten`) had merged
   to `main` (documented in your PR description with the `gh pr list`/`git
   log` output you re-ran at the time).
2. **O0's decision note is in place** — both modules' docstrings clearly
   state their scope and cross-reference each other; no code was merged
   unnecessarily.
3. **O1** — `OrderManager`'s reconciliation-drift alert routes through
   `observability.alerts.send_alert` (verified by a real test asserting the
   call, not just a manual read).
4. **O2** — dedup/rate-limiting exists, is purely additive (`dedup_key`
   opt-in), and is covered by tests.
5. **O3** — kill-switch activation, missing/non-deployable validation
   reports, and portfolio-heat/correlation-concentration WARNINGs actually
   fire `observability.alerts.send_alert` in the live pipeline (not just in
   a preflight CLI check), each covered by a test asserting the call.
6. **O4** — a channel health-check exists and is wired as a warning-only
   preflight check.
7. **`mcp__investyo__run_platform_tests` (or `pytest -q`) is green.**
8. The **four still-undocumented triggers** (broker connection lost, large
   fill slippage, order filled, daily rebalance complete) are explicitly
   listed as deferred/future work in your final PR description — not
   silently dropped, not force-wired to a fabricated call site.
