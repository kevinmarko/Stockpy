"""
InvestYo Quant Platform — Alerting / Notification Dispatcher
=============================================================
Lightweight, multi-channel notification system for the cloud-hosted
pipeline. Supports Ntfy.sh (free push), Email (SMTP), and Slack webhooks.

Configuration lives in .env:
    ALERT_NTFY_TOPIC=investyo-alerts
    ALERT_EMAIL_TO=beforecoast@gmail.com
    ALERT_EMAIL_FROM=investyo-alerts@gmail.com
    ALERT_EMAIL_SMTP_HOST=smtp.gmail.com
    ALERT_EMAIL_SMTP_PORT=587
    ALERT_EMAIL_SMTP_PASSWORD=<app-password>
    ALERT_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
    ALERT_CHANNELS=ntfy,email   # comma-separated active channels
"""

import os
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)

# ── Channel Implementations ──────────────────────────────────────────────────

def _send_ntfy(title: str, message: str, priority: str = "default") -> bool:
    """Send a push notification via ntfy.sh (free, no account required)."""
    topic = os.getenv("ALERT_NTFY_TOPIC", "investyo-alerts")
    url = f"https://ntfy.sh/{topic}"

    priority_map = {"low": "2", "default": "3", "high": "4", "urgent": "5"}
    ntfy_priority = priority_map.get(priority, "3")

    try:
        req = Request(url, data=message.encode("utf-8"), method="POST")
        req.add_header("Title", title)
        req.add_header("Priority", ntfy_priority)
        req.add_header("Tags", "chart_with_upwards_trend")
        with urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Ntfy notification sent: %s", title)
                return True
            logger.warning("Ntfy returned status %d", resp.status)
            return False
    except (URLError, OSError) as exc:
        logger.error("Ntfy send failed: %s", exc)
        return False


def _send_email(title: str, message: str, priority: str = "default") -> bool:
    """Send an email notification via SMTP."""
    smtp_host = os.getenv("ALERT_EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("ALERT_EMAIL_SMTP_PORT", "587"))
    smtp_password = os.getenv("ALERT_EMAIL_SMTP_PASSWORD")
    email_from = os.getenv("ALERT_EMAIL_FROM", "investyo-alerts@gmail.com")
    email_to = os.getenv("ALERT_EMAIL_TO", "beforecoast@gmail.com")

    if not smtp_password:
        logger.warning("ALERT_EMAIL_SMTP_PASSWORD not set; skipping email")
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = email_from
        msg["To"] = email_to
        msg["Subject"] = f"[InvestYo] {title}"
        if priority in ("high", "urgent"):
            msg["X-Priority"] = "1"

        msg.attach(MIMEText(message, "plain"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(email_from, smtp_password)
            server.send_message(msg)

        logger.info("Email notification sent: %s", title)
        return True
    except Exception as exc:
        logger.error("Email send failed: %s", exc)
        return False


def _send_slack(title: str, message: str, priority: str = "default") -> bool:
    """Send a Slack notification via incoming webhook."""
    webhook_url = os.getenv("ALERT_SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("ALERT_SLACK_WEBHOOK_URL not set; skipping Slack")
        return False

    emoji = {"low": "ℹ️", "default": "📊", "high": "⚠️", "urgent": "🚨"}.get(priority, "📊")
    payload = json.dumps({
        "text": f"{emoji} *{title}*\n{message}",
    }).encode("utf-8")

    try:
        req = Request(webhook_url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Slack notification sent: %s", title)
                return True
            logger.warning("Slack returned status %d", resp.status)
            return False
    except (URLError, OSError) as exc:
        logger.error("Slack send failed: %s", exc)
        return False


# ── Dispatcher ────────────────────────────────────────────────────────────────

CHANNEL_HANDLERS = {
    "ntfy": _send_ntfy,
    "email": _send_email,
    "slack": _send_slack,
}


def get_active_channels() -> List[str]:
    """Returns the list of active alert channels from .env."""
    raw = os.getenv("ALERT_CHANNELS", "ntfy")
    return [ch.strip().lower() for ch in raw.split(",") if ch.strip()]


def send(
    title: str,
    message: str,
    *,
    priority: str = "default",
    channels: Optional[List[str]] = None,
) -> dict:
    """
    Dispatch a notification to all active channels.

    Args:
        title: Short notification title (e.g. "Daily Pipeline Complete").
        message: Full notification body.
        priority: One of 'low', 'default', 'high', 'urgent'.
        channels: Override active channels. If None, reads from .env.

    Returns:
        Dict mapping channel name → bool (success/failure).
    """
    active = channels or get_active_channels()
    results = {}

    for channel in active:
        handler = CHANNEL_HANDLERS.get(channel)
        if handler is None:
            logger.warning("Unknown alert channel: %s", channel)
            results[channel] = False
            continue
        try:
            results[channel] = handler(title, message, priority)
        except Exception as exc:
            logger.error("Channel %s raised: %s", channel, exc)
            results[channel] = False

    return results


# ── Alert Configuration Store ─────────────────────────────────────────────────

_ALERT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "alert_config.json"
)


def get_alert_config() -> dict:
    """Load the current alert configuration from disk."""
    if os.path.exists(_ALERT_CONFIG_PATH):
        try:
            with open(_ALERT_CONFIG_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "channels": get_active_channels(),
        "events": {
            "signal_fired": True,
            "model_stale": True,
            "pipeline_failed": True,
            "pit_audit_failed": True,
        },
    }


def save_alert_config(config: dict) -> None:
    """Persist the alert configuration to disk."""
    with open(_ALERT_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
