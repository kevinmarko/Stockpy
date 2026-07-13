"""
alerting.py — Structured logging setup and push-notification dispatcher
=======================================================================
Responsibilities
----------------
1. setup_logging()   — configure root logging ONCE: a structured
                        timestamp/level/module/message formatter, a
                        RotatingFileHandler writing to logs/investyo.log
                        (10 MB × 5 backups, UTF-8), and a StreamHandler
                        to stderr.  Safe to call multiple times — the
                        function is idempotent via a root-handler guard.

2. notify()          — POST a push notification to ntfy.sh using the
                        NTFY_TOPIC env var.  Returns immediately (no-op)
                        when NTFY_TOPIC is unset.  Network failures are
                        caught and logged as WARNING — the app never
                        crashes on a failed push.  Secrets MUST NOT be
                        passed in title or message.

3. summarize_run()   — return a short human-readable text summary of a
                        RunResult, suitable for both logging and ntfy
                        notification bodies.  Designed to avoid a
                        circular import: accepts any duck-typed object
                        with recommendations / errors / started_at /
                        duration_seconds attributes (matching RunResult).

Usage in main.py
----------------
    from alerting import notify, setup_logging, summarize_run

    def main() -> None:
        setup_logging()          # ← very first call in main()
        ...
        result = run_once(...)
        summary = summarize_run(result)
        logger.info("\\n%s", summary)
        if result.errors:
            notify("InvestYo ⚠ Errors", ..., priority="high")
        elif first_clean_run:
            notify("InvestYo ✓ Complete", summary, priority="default")

Two-system note (observability/alerts.py)
------------------------------------------
This module is narrowly scoped to ``main.py``'s advisory-loop mobile push
notification (ntfy.sh) and root-logger setup for that one entry point. It is
a **separate, parallel** system from ``observability/alerts.py`` — the
general multi-channel (console/file/Discord/Slack/email) alert dispatcher
used by strategy/risk/execution-layer code, ``prompt_registry``, and
``validation/drift``. The two modules are deliberately **not merged**: a
personal phone push (this module's ``notify()``) and a team/ops-channel
dispatcher (``observability/alerts.py``) serve genuinely different audiences
and have no shared code path. If you are adding a new alert trigger outside
``main.py``'s advisory loop, use ``observability.alerts.send_alert()``
instead of this module. See ``observability/alerts.py``'s module docstring
for its own side of this cross-reference.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------
_LOGS_DIR = Path("logs")
_LOG_FILE = _LOGS_DIR / "investyo.log"
_LOG_MAX_BYTES = 10 * 1024 * 1024   # 10 MB per rotating segment
_LOG_BACKUP_COUNT = 5               # keep 5 rotated files → 50 MB max on disk
_NTFY_BASE_URL = "https://ntfy.sh"
_NTFY_REQUEST_TIMEOUT_S = 10

# Valid ntfy priority strings (https://docs.ntfy.sh/publish/#message-priority)
_VALID_PRIORITIES = frozenset({"max", "urgent", "high", "default", "low", "min"})


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------

def setup_logging(log_level: str = "INFO") -> None:
    """Configure the root logger with a rotating file handler and a console handler.

    Idempotent: if handlers are already attached to the root logger this
    function returns immediately so it is safe to call in tests, at import
    time, or from multiple entry points.

    The LOG_LEVEL environment variable overrides the ``log_level`` argument.

    Handler layout
    --------------
    RotatingFileHandler : logs/investyo.log, UTF-8, 10 MB, 5 backups.
                          The ``logs/`` directory is created automatically.
    StreamHandler       : stderr, same level and formatter as the file.

    Formatter
    ---------
    ``"YYYY-MM-DD HH:MM:SS  LEVEL     module.submodule — message"``

    Parameters
    ----------
    log_level :
        Default minimum level string (``"DEBUG"``/``"INFO"``/``"WARNING"``…).
        Overridden by the ``LOG_LEVEL`` env var when set.
    """
    root = logging.getLogger()
    if root.handlers:
        # Already configured — adding handlers again would duplicate log lines.
        return

    effective_str = os.environ.get("LOG_LEVEL", log_level).upper()
    numeric_level = getattr(logging, effective_str, logging.INFO)
    root.setLevel(numeric_level)

    formatter = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Rotating file handler ─────────────────────────────────────────────────
    try:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(_LOG_FILE),
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(numeric_level)
        root.addHandler(file_handler)
    except OSError as exc:
        # Unable to write to disk (permissions, read-only FS, etc.).
        # Fall through to console-only — never crash the app over logging.
        print(f"[alerting] WARNING: could not create rotating log handler: {exc}")

    # ── Console (stderr) handler ──────────────────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(numeric_level)
    root.addHandler(console_handler)

    root.info(
        "Logging initialised — level=%s  file=%s",
        effective_str,
        _LOG_FILE,
    )


# ---------------------------------------------------------------------------
# notify
# ---------------------------------------------------------------------------

def notify(
    title: str,
    message: str,
    priority: str = "default",
) -> None:
    """POST a push notification to ntfy.sh via the NTFY_TOPIC environment variable.

    This is **fire-and-forget**: network failures are caught and logged as
    WARNING; they never propagate.  When ``NTFY_TOPIC`` is unset the function
    returns immediately (silent no-op) — no crash, no side-effect.

    Security invariant
    ------------------
    Secrets (API keys, passwords, MFA seeds) MUST NOT be passed in ``title``
    or ``message``.  The function adds no ``Authorization`` header; ntfy.sh
    topics are access-controlled by the topic name alone (keep the topic
    unguessable, or run a self-hosted server with token auth and add the
    ``Authorization`` header in your own wrapper).

    ntfy priority levels (low → high alert urgency)
    ------------------------------------------------
    ``"min"``     : no sound, no banner.
    ``"low"``     : no sound.
    ``"default"`` : system default sound.
    ``"high"``    : always makes a sound.
    ``"urgent"``  : bypasses Do-Not-Disturb on iOS and Android.
    ``"max"``     : same as urgent — reserved for true emergencies.

    Parameters
    ----------
    title :
        Short notification heading shown as the push title on mobile.
    message :
        Notification body.  Keep under ~1 000 characters for readability.
    priority :
        ntfy priority string (see table above).  Defaults to ``"default"``.
        Unknown strings are silently replaced with ``"default"``.
    """
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return  # NTFY_TOPIC not configured → silent no-op

    if priority not in _VALID_PRIORITIES:
        priority = "default"

    url = f"{_NTFY_BASE_URL}/{topic}"
    body = message.encode("utf-8")

    req = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={
            "Title": title,
            "Priority": priority,
            "Content-Type": "text/plain; charset=utf-8",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=_NTFY_REQUEST_TIMEOUT_S) as resp:
            if resp.status not in (200, 201):
                logger.warning(
                    "ntfy POST returned unexpected HTTP %d for topic '%s'.",
                    resp.status,
                    topic,
                )
            else:
                logger.debug("ntfy push sent (priority=%s title=%r).", priority, title)
    except urllib.error.URLError as exc:
        logger.warning("ntfy notification failed (network error): %s", exc)
    except Exception as exc:
        # Catch-all: never let a failed push crash the analysis pipeline.
        logger.warning("ntfy notification failed (unexpected): %s", exc)


# ---------------------------------------------------------------------------
# summarize_run
# ---------------------------------------------------------------------------

def summarize_run(result: Any) -> str:
    """Produce a short, human-readable summary of a RunResult.

    Accepts any duck-typed object with attributes matching ``main.RunResult``:
    ``recommendations``, ``errors``, ``started_at``, ``duration_seconds``.
    Using ``Any`` avoids a circular import (``main.py`` imports this module).

    The returned string is suitable for:
    * An ``INFO`` log line after each pipeline cycle.
    * An ntfy push-notification body (keep under 1 KB).

    Example output
    --------------
    ::

        InvestYo Run — 2026-06-25 09:35:01 UTC  (8.4 s)
        Universe: 12 evaluated  (11 OK, 1 error)
        Signals : BUY=4  HOLD=6  SELL=1
        Errors  : 1  (TSLA @ advisory_evaluate)
        ── Top 3 actionable ──────────────────────────────────
          1. BUY  AAPL     conviction=0.82  pos=4.5%  "Strong momentum with..."
          2. BUY  MSFT     conviction=0.71  pos=3.2%  "Multifactor score high..."
          3. SELL INTC     conviction=0.65  pos=0.0%  "Below cost basis, bearish..."

    Parameters
    ----------
    result :
        A ``RunResult`` (or any object with the same attribute shape).

    Returns
    -------
    str
        Multi-line summary string; never raises.
    """
    recs = getattr(result, "recommendations", [])
    errors = getattr(result, "errors", [])
    started_at = getattr(result, "started_at", None)
    duration = float(getattr(result, "duration_seconds", 0.0))

    n_total = len(recs) + len(errors)
    n_ok = len(recs)
    n_err = len(errors)

    buys  = sum(1 for r in recs if getattr(r, "action", "") == "BUY")
    holds = sum(1 for r in recs if getattr(r, "action", "") == "HOLD")
    sells = sum(1 for r in recs if getattr(r, "action", "") == "SELL")

    ts_str = (
        started_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if started_at is not None
        else "unknown"
    )

    lines: list[str] = [
        f"InvestYo Run — {ts_str}  ({duration:.1f} s)",
        f"Universe: {n_total} evaluated  ({n_ok} OK, {n_err} error{'s' if n_err != 1 else ''})",
        f"Signals : BUY={buys}  HOLD={holds}  SELL={sells}",
    ]

    if errors:
        err_preview = ", ".join(
            f"{e.get('symbol', '?')} @ {e.get('stage', '?')}"
            for e in errors[:3]
        )
        suffix = f" … +{n_err - 3} more" if n_err > 3 else ""
        lines.append(f"Errors  : {n_err}  ({err_preview}{suffix})")
    else:
        lines.append("Errors  : 0  (clean run)")

    # ── Top 3 highest-conviction actionable recommendations ───────────────────
    actionable = [
        r for r in recs
        if getattr(r, "action", "HOLD") in ("BUY", "SELL")
    ]
    actionable.sort(key=lambda r: getattr(r, "conviction", 0.0), reverse=True)
    top3 = actionable[:3]

    if top3:
        lines.append("── Top 3 actionable ──────────────────────────────────")
        for i, r in enumerate(top3, start=1):
            symbol    = getattr(r, "symbol", "?")
            action    = getattr(r, "action", "?")
            conv      = float(getattr(r, "conviction", 0.0))
            pct       = float(getattr(r, "suggested_position_pct", 0.0)) * 100.0
            rationale = str(getattr(r, "rationale", ""))[:60]
            lines.append(
                f"  {i}. {action:<4} {symbol:<8} "
                f"conviction={conv:.2f}  pos={pct:.1f}%  \"{rationale}\""
            )

    return "\n".join(lines)
