---
name: alert-rule-authoring
description: >-
  Author, wire up, or debug an alert/notification in this platform. Use when
  adding a new alert trigger, adjusting an alert threshold, wiring
  ALERT_WEBHOOK_URL / a Discord/Slack webhook, or debugging why an alert
  fired or didn't -- covers the THREE real, separate alert systems in this
  codebase (root alerting.py's ntfy push, observability/alerts.py's
  multi-channel dispatcher, alerting_mcp/notifier.py behind the MCP
  configure_alerts/send_test_alert tools) and which one a new trigger
  actually belongs in.
---

<!--
  Ported from this repo's Claude Code sibling skill (`.claude/skills/alert-rule-authoring/SKILL.md`)
  to Antigravity's skill format. Frontmatter and body content are carried over verbatim --
  Antigravity's own `google-antigravity-sdk` skill and this repo's existing `.agents/skills/supabase`
  skill both use the same minimal `name` + `description` frontmatter shape Claude's SKILL.md already
  used here, so no restructuring was required for this port beyond this note.
-->

# Authoring an alert rule

**This repo has three separate, deliberately-not-merged alerting systems.**
Each module's own docstring cross-references the others and states this
explicitly — read the relevant one before assuming there's a single
"alerting.py" to edit. Picking the wrong one is the most common mistake
here.

| System | Module | Audience / trigger | Channels |
|---|---|---|---|
| Advisory-loop mobile push | `alerting.py` | `main.py`'s own run loop only | ntfy.sh (`notify()`) |
| General multi-channel dispatcher | `observability/alerts.py` | Strategy/risk/execution-layer code, `prompt_registry`, `validation/drift` | console, file, Discord, Slack, email |
| MCP-tool-facing notifier | `alerting_mcp/notifier.py` | The `configure_alerts`/`send_test_alert` MCP tools (`investyo_mcp_server.py`) | ntfy, email, Slack |
| Legacy reconciliation webhook | `execution/order_manager.py` (`_send_alert`, uses `settings.ALERT_WEBHOOK_URL`) | Broker-vs-internal position drift only | one Slack/Discord incoming webhook |

## 1. Deciding which system a new alert belongs in

- **A personal mobile push tied to `main.py`'s own advisory-loop cycle**
  (e.g. "notify me when this run completes/errors") → `alerting.notify()`.
  Reads `NTFY_TOPIC` from `os.environ` directly (not `settings.X` — this
  module predates the `settings.X`-only convention documented elsewhere in
  this repo; don't "fix" it without checking `tests/test_alerting.py` first,
  since `main.py`'s own call sites may depend on the current read path).
  Silent no-op when `NTFY_TOPIC` is unset. **Never pass secrets in `title`/
  `message`** — no `Authorization` header is added; ntfy topics are
  access-controlled by name alone.
- **Anything else — strategy/risk/execution-layer, a new CRITICAL/WARNING/
  INFO condition anywhere outside `main.py`'s own loop** →
  `observability.alerts.send_alert(level, message, channels=None, extra=None,
  dedup_key=None)`. This is the module's own explicit instruction: *"If you
  are adding a new alert trigger outside `main.py`'s advisory loop, use
  `observability.alerts.send_alert()` instead of this module."*
  - `level` is one of `"INFO"` / `"WARNING"` / `"CRITICAL"` (a `Literal`
    type, not a free string). The module docstring's own contract: CRITICAL
    = kill switch activated, reconciliation drift, broker connection lost,
    missing/non-deployable validation report; WARNING = portfolio heat
    approaching limit (>5%), single-name correlation concentration, large
    fill slippage vs. the expected model cost; INFO = order filled, daily
    rebalance complete, daily summary. Match a new trigger's severity to
    this table rather than inventing a new tier.
  - Every channel write is wrapped in a broad `except Exception` inside
    `send_alert()` itself — a broken webhook/full disk/unreachable SMTP
    server logs an `ERROR` and never propagates into the trading pipeline.
    You do not need your own try/except around a `send_alert()` call for
    this reason (though the outer call site should still guard against
    `send_alert` itself being unimportable — see `order_manager.py`'s own
    lazy-import-plus-`except Exception` pattern for the convention).
  - Channels are active based on `settings.*` at **dispatch time**, not
    import time: `settings.ALERT_FILE_PATH`, `settings.DISCORD_WEBHOOK_URL`,
    `settings.SLACK_WEBHOOK_URL`, and all three of
    `settings.ALERT_SMTP_HOST`/`ALERT_EMAIL_FROM`/`ALERT_EMAIL_TO` together
    for email (a partial email config is silently ignored, not an error).
    `console` is always active (the last-resort audit trail).
  - **Dedup**: pass `dedup_key` to suppress a repeat of the *same* condition
    within `settings.ALERT_DEDUP_WINDOW_SECONDS` (default 900s/15min) — this
    is the fix for "an alert condition that stays true fires every cycle"
    (the classic alert-storm failure mode). State is in-process only
    (`dict[str, float]` of `time.monotonic()` timestamps), never persisted —
    a restart clears it, which is the conservative direction. Omitting
    `dedup_key` reproduces the pre-dedup always-fires behavior exactly.
    `reset_dedup_state()` exists for test isolation only.
  - `send_daily_summary(pnl_summary, warnings)` and `check_channel_health()`
    are the other two public entry points — read their docstrings before
    reimplementing either.
- **You're changing what the MCP `configure_alerts`/`send_test_alert` tools
  do** → that's `alerting_mcp/notifier.py`, a THIRD, simpler module — do not
  assume it's the same as either of the above. It reads its channel config
  via plain `os.getenv(...)` (`ALERT_NTFY_TOPIC`, `ALERT_EMAIL_TO`,
  `ALERT_EMAIL_FROM`, `ALERT_EMAIL_SMTP_HOST`/`_PORT`/`_PASSWORD`,
  `ALERT_SLACK_WEBHOOK_URL`, `ALERT_CHANNELS`) rather than `settings.X` — be
  aware this means a value that exists only in `.env` (never exported to the
  real shell environment) will not be seen here unless something upstream
  calls `load_dotenv()` first (see CLAUDE.md's ".env resolution fix" bullet
  for the general shape of this bug class; this module has not been
  confirmed migrated to `settings.X`, so verify against the current source
  before assuming it has been fixed). `get_alert_config()`/
  `save_alert_config(config)` persist the event-subscription config
  (`signal_fired`/`model_stale`/`pipeline_failed`/`pit_audit_failed`
  booleans + active `channels` list) to `alert_config.json` at the repo
  root. `send(title, message, priority="default", channels=None)` dispatches
  to each active channel's `_send_*` handler and returns a `dict[str, bool]`
  of per-channel success — this is what `send_test_alert` reports back to
  the caller. This module is documented in
  `docs/architecture/observability-and-apis.md`'s "Alerting companion" note
  — read it for the full tool-wiring detail before changing this file.

## 2. `ALERT_WEBHOOK_URL` (the legacy reconciliation webhook)

This is CLAUDE.md's documented one: *"Set `ALERT_WEBHOOK_URL` in `.env` to a
Slack/Discord incoming-webhook URL; `reconcile_state` fires it on any
position drift."* Concretely, this lives in `execution/order_manager.py`
(NOT `alerting.py`) — `OrderManager.__init__` reads
`getattr(settings, "ALERT_WEBHOOK_URL", None)` into `self._alert_url`, and
`reconcile_state`'s drift-alert path does **two** things on drift, in order:
(1) always calls `observability.alerts.send_alert("CRITICAL", message,
extra={...})` (the multi-channel dispatcher from §1, wrapped in its own
`try/except` so a broken `observability` import can't crash reconciliation),
then (2) if `self._alert_url` is set, POSTs `{"text": message}` via
`urllib.request` (stdlib, not `requests` — this repo avoids the extra
dependency for a single POST call) with a 5-second timeout, kept purely for
backward compatibility. Both paths independently catch and log failures —
"failures logged but never swallowed silently in a bare except" per
CLAUDE.md, meaning every failure path has an explicit `logger.warning(...)`
call, not a bare `except: pass`.

## 3. Testing an alert

```bash
# Via the MCP tool (alerting_mcp/notifier.py path):
# send_test_alert(title="Test Alert", message="...") -- dispatches to every
# channel in get_alert_config()["channels"] and reports per-channel success.

# Schema/config sanity check (does not itself send anything):
make verify
```

There is no standalone CLI for `observability.alerts.send_alert()` directly
— exercise it via `pytest` against the module (check for
`tests/test_observability_alerts.py`-style coverage) or via whatever code
path you're adding the trigger to.

## 4. Common failure modes & fixes

**Alert fires every cycle for a condition that's still true (alert storm).**
If this is going through `observability.alerts.send_alert()`, the fix is to
pass a stable `dedup_key` (e.g. `f"portfolio_heat_{symbol}"`) — see §1's
dedup bullet, this is a first-class, already-implemented feature, not
something you need to build. If it's going through `alerting.notify()` or
`alerting_mcp.notifier.send()` instead, neither of those has a built-in
dedup mechanism — implement an edge-triggered check (only call `notify`/
`send` on a state transition into the alerting condition) at the call site
rather than inside either module.

**A new alert never arrives and nothing in the logs explains why.** Check,
in order: (1) is the relevant channel's setting actually populated
(`settings.DISCORD_WEBHOOK_URL`/`SLACK_WEBHOOK_URL` for
`observability.alerts`; the module's channel list is evaluated at dispatch
time, so a value set after import is still picked up — this rules out an
import-order bug); (2) for `alerting_mcp.notifier`, is the value actually in
the real process environment (see the `os.getenv` caveat in §1 — a `.env`
-only value may not be visible here); (3) is a `dedup_key` silently
suppressing it because a prior identical-key call fired within the last
`ALERT_DEDUP_WINDOW_SECONDS`.

**You're tempted to add a Telegram/PagerDuty/SMS channel.** Neither of the
two general dispatchers implements this today — `observability/alerts.py`
is architected for exactly this extension (its own docstring: "Adding a new
channel ... requires only a new `_send_*` function and a registration line
in `_active_channels()`"), so extend that one, not `alerting.py` (scoped
narrowly to ntfy) or `alerting_mcp/notifier.py` (a separate, simpler system
serving only the MCP tools).
