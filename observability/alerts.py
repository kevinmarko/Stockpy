"""
observability/alerts.py
=======================
Unified alert dispatch for the InvestYo platform.

Design
------
The module is intentionally channel-agnostic: callers state *what* happened and
*how severe* it is; this module decides *where* to send the notification based
on which channels are configured in ``.env``.  Adding a new channel (e.g.
PagerDuty, SMS) requires only a new ``_send_*`` function and a registration
line in ``_active_channels()``.

Failure isolation invariant
---------------------------
Every channel write is wrapped in a broad ``except Exception`` inside
``send_alert()``.  This is deliberate and load-bearing: a broken webhook
URL, a full disk, or a momentarily-unreachable SMTP server must *never*
propagate an exception back into the trading pipeline.  The only consequence
of a failed alert dispatch is a ``logger.error`` line — the pipeline
continues without interruption.

HTTP dependency
---------------
Webhook channels use ``urllib.request`` (stdlib) instead of ``requests`` to
avoid a paid/optional dependency.  The free tier constraint (CONSTRAINT #1)
makes adding ``requests`` undesirable just for one POST call.

Supported channels (all optional, controlled by `settings.*`):

  console   — module-level ``logging``, always active.
  file      — JSON-lines appended to ``settings.ALERT_FILE_PATH``.
  discord   — HTTP POST to ``settings.DISCORD_WEBHOOK_URL``.
  slack     — HTTP POST to ``settings.SLACK_WEBHOOK_URL``.
  email     — SMTP via ``settings.ALERT_SMTP_*`` settings (STARTTLS on 587).

Public API
----------
``send_alert(level, message, channels=None, extra=None, dedup_key=None)``
    Dispatch a single alert.  ``channels=None`` uses every active channel.
    Pass ``dedup_key`` to suppress repeated identical-condition alerts within
    ``settings.ALERT_DEDUP_WINDOW_SECONDS`` — see "Dedup / rate-limiting"
    below.

``send_daily_summary(pnl_summary, warnings)``
    Compose and dispatch a structured end-of-day summary.  Called from the
    orchestrator (or a cron job) after the last pipeline run of the session.

``check_channel_health()``
    Probe every currently-active channel with a lightweight, clearly-labeled
    test dispatch and report per-channel reachability.  See "Channel
    health-check" below.

Alert-level contract (caller's responsibility to evaluate conditions):
  CRITICAL — kill switch activated, reconciliation drift detected, broker
             connection lost, missing/non-deployable validation report.
  WARNING  — portfolio heat approaching limit (>5%), single-name correlation
             concentration, large fill slippage versus the expected model cost.
  INFO     — order filled, daily rebalance complete, daily summary.

Dedup / rate-limiting
----------------------
``send_alert()`` accepts an optional ``dedup_key: str`` parameter.  When
supplied, a second call with the same key within
``settings.ALERT_DEDUP_WINDOW_SECONDS`` (default 900s / 15 min) is suppressed
(logged at DEBUG, dispatched to no channel) rather than re-firing an
identical alert every time the caller re-evaluates a still-true condition
(e.g. sustained portfolio heat on every pipeline cycle — the classic alert-
storm failure mode). Dedup state is an in-process ``dict[str, float]``
keyed by ``dedup_key`` (``time.monotonic()`` timestamps, never persisted to
disk), matching this codebase's existing in-process-cache convention (see
``data/market_data.py``'s ``_BarsCache``/``_FundamentalsCache``). This is
intentionally NOT a durable audit trail — the ``file`` channel already
provides that. Omitting ``dedup_key`` (the default) reproduces the exact
pre-dedup always-fires behavior; this feature is purely additive.
``reset_dedup_state()`` clears all dedup state — intended for test isolation.

Two-system note (root ``alerting.py``)
---------------------------------------
This module is the general multi-channel dispatcher for strategy / risk /
execution-layer alerts (CRITICAL / WARNING / INFO fanned out to Discord,
Slack, email, the JSON-lines file log, and console). Root-level
``alerting.py`` is a *separate, narrower* module: it owns ``main.py``'s
advisory-loop mobile push notification (ntfy.sh) plus root-logger setup for
that same entry point. The two modules are deliberately **not merged** — they
serve genuinely different audiences and channels (a personal phone push vs.
a team/ops channel dispatcher) and share no code. If you are adding a new
alert trigger, use *this* module unless the alert is specifically a
personal mobile push tied to ``main.py``'s advisory loop, in which case use
``alerting.notify()`` instead. See ``alerting.py``'s module docstring for its
own side of this cross-reference.
"""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Literal, Optional

from settings import settings

logger = logging.getLogger(__name__)

# ``AlertLevel`` is a string literal type so callers get IDE auto-complete and
# mypy/pyright catch typos at type-check time rather than at runtime.
AlertLevel = Literal["INFO", "WARNING", "CRITICAL"]

# Emoji prefix per level makes Discord / Slack feeds easy to scan at a glance
# without having to read the bracketed level tag.
_LEVEL_EMOJI: dict[str, str] = {
    "INFO": "ℹ️",
    "WARNING": "⚠️",
    "CRITICAL": "🚨",
}

# Canonical channel names understood by ``send_alert``.  Listed for reference
# and for callers that want to enumerate all known channels.
ALL_CHANNELS = ("console", "file", "discord", "slack", "email")

# In-process TTL dedup state: dedup_key -> time.monotonic() of last DISPATCHED
# (i.e. not itself suppressed) alert with that key. Never persisted to disk —
# see the module docstring's "Dedup / rate-limiting" section. A fresh process
# (e.g. after a restart) starts with empty state, which is the conservative
# direction (a real condition can never be permanently silenced by a stale
# on-disk timestamp).
_dedup_state: dict[str, float] = {}


def reset_dedup_state() -> None:
    """Clear all in-process alert-dedup state.

    Intended for test isolation (mirrors ``data/market_data.py``'s
    ``reset_provider()`` pattern) — call this in a test's setup/teardown so
    one test's ``dedup_key`` usage cannot suppress another test's alert.
    """
    _dedup_state.clear()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _active_channels() -> list[str]:
    """Return every channel that currently has valid runtime configuration.

    This is evaluated at *dispatch time* (not at import time) so that changes
    to settings — e.g. a webhook URL set after module load in tests — are
    always reflected.  ``console`` is unconditionally included because it
    requires no configuration and is the last-resort audit trail.

    Email requires all three of host, sender, and recipient list to be
    configured; a partial email config is silently ignored rather than raising
    so that an operator who sets only ``ALERT_SMTP_HOST`` by mistake doesn't
    accidentally disable all email alerts by triggering an error here.
    """
    active = ["console"]
    if settings.ALERT_FILE_PATH:
        active.append("file")
    if settings.DISCORD_WEBHOOK_URL:
        active.append("discord")
    if settings.SLACK_WEBHOOK_URL:
        active.append("slack")
    if all([settings.ALERT_SMTP_HOST, settings.ALERT_EMAIL_FROM, settings.ALERT_EMAIL_TO]):
        active.append("email")
    return active


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_alert(
    level: AlertLevel,
    message: str,
    channels: Optional[list[str]] = None,
    extra: Optional[dict[str, Any]] = None,
    dedup_key: Optional[str] = None,
) -> None:
    """Dispatch an alert to one or more output channels.

    Parameters
    ----------
    level:
        Severity string — "INFO", "WARNING", or "CRITICAL".
    message:
        Human-readable alert body.  Keep to a single paragraph; the daily
        summary uses a multi-line format via ``send_daily_summary`` instead.
    channels:
        Explicit list of channel names to target.  Pass ``None`` (default)
        to send to every active channel as determined by ``_active_channels()``.
        Passing an explicit list is intended for tests and for callers that
        want to, e.g., send CRITICAL alerts to email only.
    extra:
        Optional structured context included verbatim in file/webhook payloads.
        Use this for machine-parseable metadata (symbol, strategy_id, etc.)
        that should be present in the JSON record but need not appear in the
        human-readable ``message``.
    dedup_key:
        Optional opt-in suppression key (see module docstring's "Dedup /
        rate-limiting" section).  When provided and an alert with the same
        key was already dispatched within ``settings.ALERT_DEDUP_WINDOW_SECONDS``,
        this call is suppressed entirely (logged at DEBUG only — no channel
        is touched). ``None`` (the default) means "always fire", identical to
        this function's behavior before dedup existed. Use a key that is
        stable for the *condition*, not the individual event, e.g.
        ``dedup_key="kill_switch_activate"`` or ``dedup_key=f"heat_{symbol}"``.

    Side effects
    ------------
    * Writes a JSON-lines entry to ``settings.ALERT_FILE_PATH`` if the file
      channel is active.
    * Makes outbound HTTP POST requests to webhook URLs.
    * Sends an SMTP email if the email channel is configured.

    This function *never raises*.  Any channel-level error is caught, logged
    at ERROR level, and discarded so the calling pipeline is not interrupted.
    """
    if dedup_key is not None:
        now = time.monotonic()
        last = _dedup_state.get(dedup_key)
        window = settings.ALERT_DEDUP_WINDOW_SECONDS
        if last is not None and (now - last) < window:
            logger.debug(
                "send_alert: suppressed duplicate [dedup_key=%s level=%s] — "
                "last fired %.1fs ago (window=%ss)",
                dedup_key, level, now - last, window,
            )
            return
        _dedup_state[dedup_key] = now

    ts = datetime.now(timezone.utc).isoformat()
    targets = channels if channels is not None else _active_channels()

    # Build the structured payload once; each channel formatter can pull from
    # it rather than reconstructing the same dict independently.
    payload: dict[str, Any] = {
        "timestamp": ts,
        "level": level,
        "message": message,
        **(extra or {}),
    }

    for ch in targets:
        try:
            if ch == "console":
                _send_console(level, ts, message)
            elif ch == "file":
                _send_file(payload)
            elif ch == "discord":
                _send_discord(level, ts, message, extra)
            elif ch == "slack":
                _send_slack(level, ts, message, extra)
            elif ch == "email":
                _send_email(level, message, extra)
            else:
                # Unknown channel names are logged but never raise; a typo in
                # an explicit ``channels`` list shouldn't suppress other alerts.
                logger.warning("send_alert: unknown channel %r — skipped", ch)
        except Exception as exc:
            # Broad catch is intentional — see module docstring.  We log with
            # full context so the operator can diagnose the channel failure
            # without the pipeline knowing it happened.
            logger.error(
                "Alert dispatch failed [channel=%s level=%s]: %s", ch, level, exc
            )


def send_daily_summary(
    pnl_summary: dict[str, Any],
    warnings: list[str],
) -> None:
    """Compose and send the end-of-day summary to all configured channels.

    The summary is intentionally structured as a human-readable Markdown
    block so it renders well in both Discord (which renders Markdown) and
    plain-text email.

    Parameters
    ----------
    pnl_summary:
        Mapping of strategy_id → net realized P&L float for the session.
        Pass an empty dict if no trades were closed (produces a "no closed
        trades today" line rather than an empty section).
    warnings:
        Non-blocking operational warnings accumulated during the trading day
        (e.g. "correlation check required manual override on AAPL").  Pass
        an empty list for a clean day.

    Notes
    -----
    The structured P&L data is also included in the ``extra`` argument so
    downstream JSON consumers can parse it from the file channel without
    having to parse the formatted string.
    """
    ts = datetime.now(timezone.utc).date().isoformat()
    lines = [f"**InvestYo Daily Summary — {ts}**", ""]

    lines.append("**P&L by strategy:**")
    if pnl_summary:
        for strat, pnl in pnl_summary.items():
            sign = "+" if pnl >= 0 else ""
            lines.append(f"  • {strat}: {sign}${pnl:,.2f}")
    else:
        # Explicit "no closed trades" text avoids an empty section that could
        # be mistaken for a truncated message.
        lines.append("  • (no closed trades today)")

    lines.append("")
    if warnings:
        lines.append(f"**Warnings ({len(warnings)}):**")
        for w in warnings:
            lines.append(f"  ⚠️ {w}")
    else:
        lines.append("**Warnings:** none")

    message = "\n".join(lines)
    send_alert("INFO", message, extra={"type": "daily_summary", "pnl": pnl_summary})


def check_channel_health() -> dict[str, dict[str, Any]]:
    """Probe every currently-active channel with a lightweight test dispatch.

    For each channel returned by ``_active_channels()``, attempt a clearly-
    labeled INFO-level test send and report whether it succeeded.

    This exists because, absent this function, an operator only discovers a
    broken Discord/Slack webhook or a misconfigured SMTP relay when a REAL
    alert silently fails — the failure is caught and logged at
    ``logger.error`` inside ``send_alert()``, which is easy to miss in normal
    log volume, especially in the middle of an actual incident when the
    operator most needs the channel to work.

    Implementation note
    --------------------
    This deliberately does NOT call ``send_alert()`` itself: ``send_alert()``
    is intentionally, load-bearingly non-raising (see the module docstring's
    "Failure isolation invariant") and does not report per-channel success —
    that broad catch is exactly what makes a broken channel invisible to a
    caller in the first place. To surface per-channel ``ok``/``error``,
    ``check_channel_health()`` invokes the same ``_send_*`` implementations
    ``send_alert()`` uses internally, but with its OWN try/except per
    channel so a failure is captured rather than silently discarded.

    Returns
    -------
    dict[str, dict]
        ``{channel_name: {"ok": bool, "error": Optional[str]}}`` for every
        channel in ``_active_channels()``. An empty-ish result (just
        ``{"console": {"ok": True, "error": None}}``) means no other channel
        is currently configured — ``console`` cannot meaningfully fail
        (it is a local ``logging`` call, not network I/O).

    Notes
    -----
    Never raises — a probe failure for one channel is captured in its own
    dict entry and does not prevent the remaining channels from being probed
    (CONSTRAINT #6). This function performs real outbound network calls (for
    discord/slack/email) when those channels are configured; callers that
    want a fully offline check should mock the relevant ``_send_*`` /
    ``urllib.request.urlopen`` / ``smtplib.SMTP`` boundary, exactly as
    ``tests/test_alerts.py`` already does for the channel-level tests.
    """
    ts = datetime.now(timezone.utc).isoformat()
    message = "[Health Check] observability/alerts.py self-test — ignore"
    results: dict[str, dict[str, Any]] = {}
    for ch in _active_channels():
        try:
            if ch == "console":
                _send_console("INFO", ts, message)
            elif ch == "file":
                _send_file({
                    "timestamp": ts, "level": "INFO", "message": message,
                    "type": "channel_health_check",
                })
            elif ch == "discord":
                _send_discord("INFO", ts, message, None)
            elif ch == "slack":
                _send_slack("INFO", ts, message, None)
            elif ch == "email":
                _send_email("INFO", message, None)
            else:
                results[ch] = {"ok": False, "error": f"unknown channel {ch!r}"}
                continue
            results[ch] = {"ok": True, "error": None}
        except Exception as exc:
            logger.warning("check_channel_health: channel %r unreachable: %s", ch, exc)
            results[ch] = {"ok": False, "error": str(exc)}
    return results


# ---------------------------------------------------------------------------
# Channel implementations
# ---------------------------------------------------------------------------

def _send_console(level: AlertLevel, ts: str, message: str) -> None:
    """Dispatch to Python's logging framework at the appropriate log level.

    The log level mirrors the alert severity so operators can filter their
    log aggregator (e.g. CloudWatch, Datadog) by standard Python log levels
    rather than having to parse the alert message for the bracketed level tag.
    Unknown levels default to ``logger.info`` so nothing is silently dropped.
    """
    log_fn = {
        "INFO": logger.info,
        "WARNING": logger.warning,
        "CRITICAL": logger.critical,
    }.get(level, logger.info)
    log_fn("[ALERT %s %s] %s", level, ts, message)


def _send_file(payload: dict[str, Any]) -> None:
    """Append a JSON-lines record to the configured alert log file.

    JSON-lines format (one JSON object per line) was chosen over a plain log
    file because it is trivially machine-parseable by the preflight check and
    the Streamlit dashboard without a log-parsing library.

    ``json.dumps(..., default=str)`` serializes any non-serializable values
    (e.g. ``datetime`` objects, ``Path``s) to their string representation
    rather than raising ``TypeError``, so callers can safely put arbitrary
    objects in ``extra``.
    """
    path = settings.ALERT_FILE_PATH
    if not path:
        return
    line = json.dumps(payload, default=str) + "\n"
    # Open in append mode so each call adds a line without truncating history.
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def _send_discord(
    level: AlertLevel, ts: str, message: str, extra: Optional[dict[str, Any]]
) -> None:
    """POST an alert to a Discord incoming webhook.

    Discord's webhook API expects ``{"content": "<message string>"}`` for a
    plain-text message.  We do *not* use Discord embeds because they require
    structuring the payload differently depending on the alert type; a single
    formatted plain-text message is simpler and renders equivalently.

    Discord accepts HTTP 200 or 204 as success; anything else indicates the
    message was rejected (e.g. payload too large, invalid webhook ID) and we
    raise so the caller's ``except`` block can log it.
    """
    url = settings.DISCORD_WEBHOOK_URL
    if not url:
        # Belt-and-suspenders: _active_channels() already guards this, but
        # _send_discord can also be called directly in tests or by callers
        # that bypass send_alert().
        return
    emoji = _LEVEL_EMOJI.get(level, "")
    content = f"{emoji} **[{level}]** `{ts}`\n{message}"
    body = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Discord returned HTTP {resp.status}")


def _send_slack(
    level: AlertLevel, ts: str, message: str, extra: Optional[dict[str, Any]]
) -> None:
    """POST an alert to a Slack incoming webhook.

    Slack's webhook API expects ``{"text": "<message string>"}`` for a
    plain-text message.  The format is intentionally similar to Discord's but
    uses Slack's markdown dialect (``*bold*`` not ``**bold**``) so the
    message reads well in both.

    Slack returns HTTP 200 with body ``"ok"`` on success; a 200 with body
    ``"no_text"`` (or similar) still returns 200, which we accept.  Any
    non-200 response is treated as an error.
    """
    url = settings.SLACK_WEBHOOK_URL
    if not url:
        return
    emoji = _LEVEL_EMOJI.get(level, "")
    text = f"{emoji} *[{level}]* `{ts}`\n{message}"
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Slack returned HTTP {resp.status}")


def _send_email(
    level: AlertLevel, message: str, extra: Optional[dict[str, Any]]
) -> None:
    """Send an alert via SMTP using STARTTLS (port 587).

    STARTTLS (``smtplib.SMTP`` + ``starttls()``) is used rather than
    TLS-first / SMTPS (``smtplib.SMTP_SSL``) because port 587 with STARTTLS
    is the modern submission standard and more broadly supported by cloud SMTP
    relays (SendGrid, AWS SES, Mailgun).  If your provider requires port 465,
    replace this with ``smtplib.SMTP_SSL``.

    The ``extra`` dict is serialized as a JSON appendix in the email body so
    operators have the full structured context without needing to query the
    file log separately.

    Authentication is optional: if ``ALERT_SMTP_USER`` / ``ALERT_SMTP_PASSWORD``
    are not set, the ``login()`` call is skipped (useful for internal relay
    servers that don't require auth).
    """
    host = settings.ALERT_SMTP_HOST
    sender = settings.ALERT_EMAIL_FROM
    recipients_raw = settings.ALERT_EMAIL_TO
    if not (host and sender and recipients_raw):
        return

    # Support comma-separated recipient list so a single env var can cover
    # multiple addresses (e.g. "ops@example.com, backup@example.com").
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    subject = f"[InvestYo {level}] {message[:80]}"

    body_parts = [message]
    if extra:
        body_parts.append("\n\nAdditional context:")
        body_parts.append(json.dumps(extra, indent=2, default=str))

    msg = MIMEText("\n".join(body_parts), "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, settings.ALERT_SMTP_PORT) as smtp:
        smtp.starttls(context=ctx)
        if settings.ALERT_SMTP_USER and settings.ALERT_SMTP_PASSWORD:
            smtp.login(settings.ALERT_SMTP_USER, settings.ALERT_SMTP_PASSWORD)
        smtp.sendmail(sender, recipients, msg.as_string())
