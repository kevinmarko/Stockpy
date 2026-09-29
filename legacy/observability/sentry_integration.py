"""
observability/sentry_integration.py
===================================
Sentry error-tracking instrumentation for unattended entrypoints.

Design
------
Like the platform's alerts module (observability/alerts.py), this integration
is designed to never let an observability failure propagate back into the
trading pipeline. A failed Sentry initialization logs a WARNING and is swallowed
gracefully so the daemon continues starting up.

Dependency Isolation
--------------------
sentry_sdk is an optional dependency (see requirements-optional.txt). If the
package is not installed, initialization logs a WARNING and degrades gracefully
to a no-op, exactly matching the fallback patterns used for TensorFlow and
FinBERT.
"""

import logging

logger = logging.getLogger(__name__)


def init_sentry(*, service_name: str) -> bool:
    """
    Initialize Sentry error tracking for the given service.

    Best-effort. Never raises.

    Returns:
        bool: True if Sentry was actually initialized successfully, False otherwise.
    """
    from settings import settings

    if not settings.SENTRY_ENABLED:
        return False

    if not settings.SENTRY_DSN:
        return False

    try:
        import sentry_sdk
    except ImportError:
        logger.warning("sentry-sdk is not installed. Sentry error tracking is disabled.")
        return False

    try:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            send_default_pii=False,
        )
        sentry_sdk.set_tag("service", service_name)
        return True
    except Exception as e:
        logger.warning("Failed to initialize Sentry: %s", str(e))
        return False
