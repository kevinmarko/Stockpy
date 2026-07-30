"""
tests/test_jobs_redaction.py
=============================
Tests for api/_redact.py log scrubbing functionality.
"""

from unittest import mock

from settings import settings
from api._redact import redact_line


def test_redact_line_direct_secret_from_settings():
    """The realistic case: a secret configured only via `.env` (pydantic
    -settings loads that into `settings`, NOT into the real process
    os.environ -- see CLAUDE.md's signals/news_catalyst.py::build_finnhub_client
    precedent). redact_line must scrub it via settings.<KEY>, not os.environ."""
    with mock.patch.object(settings, "FRED_API_KEY", "super_secret_fred_key_12345"):
        line = "Error fetching FRED series using key super_secret_fred_key_12345 at endpoint"
        scrubbed = redact_line(line)
    assert "super_secret_fred_key_12345" not in scrubbed
    assert "••••[REDACTED]••••" in scrubbed


def test_redact_line_ignores_os_environ_only_value():
    """A value present in os.environ but NOT reflected on the settings
    singleton (e.g. because pydantic-settings already read a different
    value from .env at process start) must NOT be treated as configured --
    this is the inverse of the bug this file guards against: redact_line
    must never accidentally scrub unrelated text just because some env var
    happens to be set."""
    with mock.patch.dict("os.environ", {"FRED_API_KEY": "unrelated_env_only_value"}):
        line = "some log line mentioning unrelated_env_only_value in passing"
        scrubbed = redact_line(line)
    # settings.FRED_API_KEY (whatever it actually is) governs redaction, not
    # the os.environ value set above -- so this specific string survives.
    assert "unrelated_env_only_value" in scrubbed


def test_redact_line_generic_patterns():
    line1 = "Authorization: Bearer my_secret_bearer_token_xyz"
    scrubbed1 = redact_line(line1)
    assert "my_secret_bearer_token_xyz" not in scrubbed1
    assert "••••[REDACTED]••••" in scrubbed1

    line2 = "api_key='sk-proj-1234567890abcdef12345678'"
    scrubbed2 = redact_line(line2)
    assert "sk-proj-1234567890abcdef12345678" not in scrubbed2
    assert "••••[REDACTED]••••" in scrubbed2


def test_redact_line_empty_string_passthrough():
    assert redact_line("") == ""
