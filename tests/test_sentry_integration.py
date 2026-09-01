"""
tests/test_sentry_integration.py
================================
Unit tests for the Sentry error tracking integration.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from observability.sentry_integration import init_sentry


def test_init_sentry_disabled():
    """When SENTRY_ENABLED is False, returns False immediately without importing."""
    with patch("settings.settings.SENTRY_ENABLED", False):
        assert init_sentry(service_name="test_service") is False


def test_init_sentry_missing_dsn():
    """When SENTRY_DSN is absent, returns False immediately without importing."""
    with patch("settings.settings.SENTRY_ENABLED", True), \
         patch("settings.settings.SENTRY_DSN", None):
        assert init_sentry(service_name="test_service") is False


def test_init_sentry_missing_sentry_sdk(caplog):
    """When sentry_sdk is not installed, logs a WARNING and returns False gracefully."""
    # Force ImportError for sentry_sdk
    with patch.dict(sys.modules, {"sentry_sdk": None}):
        with patch("settings.settings.SENTRY_ENABLED", True), \
             patch("settings.settings.SENTRY_DSN", "fake_dsn"):
            assert init_sentry(service_name="test_service") is False
            assert "sentry-sdk is not installed" in caplog.text


def test_init_sentry_success():
    """When everything is configured and sentry_sdk is present, initializes successfully."""
    mock_sentry_sdk = MagicMock()
    with patch.dict(sys.modules, {"sentry_sdk": mock_sentry_sdk}):
        with patch("settings.settings.SENTRY_ENABLED", True), \
             patch("settings.settings.SENTRY_DSN", "fake_dsn"), \
             patch("settings.settings.SENTRY_ENVIRONMENT", "test_env"), \
             patch("settings.settings.SENTRY_TRACES_SAMPLE_RATE", 0.5):
            assert init_sentry(service_name="test_service") is True

            mock_sentry_sdk.init.assert_called_once_with(
                dsn="fake_dsn",
                environment="test_env",
                traces_sample_rate=0.5,
                send_default_pii=False,
            )
            mock_sentry_sdk.set_tag.assert_called_once_with("service", "test_service")


def test_init_sentry_exception_suppressed(caplog):
    """When sentry_sdk.init raises an exception, it is caught and logged, returning False."""
    mock_sentry_sdk = MagicMock()
    mock_sentry_sdk.init.side_effect = RuntimeError("Sentry init failed")

    with patch.dict(sys.modules, {"sentry_sdk": mock_sentry_sdk}):
        with patch("settings.settings.SENTRY_ENABLED", True), \
             patch("settings.settings.SENTRY_DSN", "fake_dsn"), \
             patch("settings.settings.SENTRY_ENVIRONMENT", "test_env"), \
             patch("settings.settings.SENTRY_TRACES_SAMPLE_RATE", 0.0):
            assert init_sentry(service_name="test_service") is False
            assert "Failed to initialize Sentry: Sentry init failed" in caplog.text
