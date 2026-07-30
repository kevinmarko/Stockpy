"""
tests/test_jobs_redaction.py
=============================
Tests for api/_redact.py log scrubbing functionality.
"""

import os
from api._redact import redact_line


def test_redact_line_direct_secret(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "super_secret_fred_key_12345")
    line = "Error fetching FRED series using key super_secret_fred_key_12345 at endpoint"
    scrubbed = redact_line(line)
    assert "super_secret_fred_key_12345" not in scrubbed
    assert "••••[REDACTED]••••" in scrubbed


def test_redact_line_generic_patterns():
    line1 = "Authorization: Bearer my_secret_bearer_token_xyz"
    scrubbed1 = redact_line(line1)
    assert "my_secret_bearer_token_xyz" not in scrubbed1
    assert "••••[REDACTED]••••" in scrubbed1

    line2 = "api_key='sk-proj-1234567890abcdef12345678'"
    scrubbed2 = redact_line(line2)
    assert "sk-proj-1234567890abcdef12345678" not in scrubbed2
    assert "••••[REDACTED]••••" in scrubbed2
