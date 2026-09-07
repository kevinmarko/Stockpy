"""
tests/test_verify_fmp_profile.py
================================
Unit tests for the scripts/verify_fmp_profile.py manual verification CLI script.
Tests CLI argument parsing, gate checks, error handling, and exit codes.
"""

from __future__ import annotations

from unittest.mock import patch
import pytest

from scripts.verify_fmp_profile import main
from settings import settings


class TestVerifyFmpProfileCLI:
    """Unit tests for verify_fmp_profile main entrypoint."""

    def test_missing_api_key_exits_code_2(self, monkeypatch, capsys):
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)

        exit_code = main(["--symbols", "AAPL"])
        assert exit_code == 2

        stderr = capsys.readouterr().err
        assert "FMP_API_KEY is not configured" in stderr

    def test_disabled_profile_exits_code_2(self, monkeypatch, capsys):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", False)

        exit_code = main(["--symbols", "AAPL"])
        assert exit_code == 2

        stderr = capsys.readouterr().err
        assert "FMP_PROFILE_ENABLED is False" in stderr

    def test_empty_symbols_exits_code_2(self, monkeypatch, capsys):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)

        exit_code = main(["--symbols", " ,  , "])
        assert exit_code == 2

        stderr = capsys.readouterr().err
        assert "--symbols resolved to an empty list" in stderr

    def test_valid_profiles_all_pass_exits_code_0(self, monkeypatch, capsys):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)

        mock_profile = {
            "symbol": "AAPL",
            "companyName": "Apple Inc.",
            "sector": "Technology",
            "description": "Apple Inc. designs, manufactures, and markets smartphones, personal computers, tablets...",
        }

        with patch("scripts.verify_fmp_profile.company_profile", return_value=mock_profile) as mock_cp:
            exit_code = main(["--symbols", "AAPL,MSFT"])
            assert exit_code == 0
            assert mock_cp.call_count == 2

        stdout = capsys.readouterr().out
        assert "[OK]" in stdout
        assert "PASS: all 2 symbol(s) successfully verified" in stdout

    def test_missing_or_short_description_exits_code_1(self, monkeypatch, capsys):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)

        short_profile = {
            "symbol": "AAPL",
            "companyName": "Apple Inc.",
            "sector": "Technology",
            "description": "Too short",  # < 20 chars
        }

        with patch("scripts.verify_fmp_profile.company_profile", return_value=short_profile):
            exit_code = main(["--symbols", "AAPL"])
            assert exit_code == 1

        stdout = capsys.readouterr().out
        assert "description missing/too short" in stdout
        assert "FAIL: 1/1 symbol(s) could not be verified" in stdout

    def test_none_profile_exits_code_1(self, monkeypatch, capsys):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)

        with patch("scripts.verify_fmp_profile.company_profile", return_value=None):
            exit_code = main(["--symbols", "AAPL"])
            assert exit_code == 1

        stdout = capsys.readouterr().out
        assert "no profile record returned" in stdout
        assert "FAIL: 1/1 symbol(s) could not be verified" in stdout

    def test_exception_in_fetch_handled_and_exits_code_1(self, monkeypatch, capsys):
        monkeypatch.setattr(settings, "FMP_API_KEY", "test-key")
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)

        with patch("scripts.verify_fmp_profile.company_profile", side_effect=RuntimeError("connection refused")):
            exit_code = main(["--symbols", "AAPL"])
            assert exit_code == 1

        stdout = capsys.readouterr().out
        assert "RuntimeError: connection refused" in stdout
        assert "FAIL: 1/1 symbol(s) could not be verified" in stdout
