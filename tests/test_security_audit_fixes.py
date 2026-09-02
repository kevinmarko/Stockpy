"""
tests/test_security_audit_fixes.py
==================================
Unit regression tests for security audit fixes:
1. Parameter validation & command injection defense in `gui/orchestrator_runner.py`.
2. Path traversal defense & directory containment in `prompt_registry/cache.py`.
3. Exception information sanitization in `pilots/run_status.py` and `pilots/prompt_registry.py`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shared.orchestrator_runner import launch_train_meta_labelers, launch_validation_run
from ml.meta_bootstrap import META_LABELED_SIGNAL_IDS
from pilots.prompt_registry import get_prompt_body
from pilots.run_status import parse_crontab_status
from prompt_registry.cache import CacheManager, _sanitize_id


# ===========================================================================
# 1. Command Injection & Input Validation (gui/orchestrator_runner.py)
# ===========================================================================

class TestLaunchValidationInputValidation:
    """Validate that launch_validation_run strictly rejects malicious or malformed inputs."""

    def test_empty_strategies_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one strategy"):
            launch_validation_run([], start="2020-01-01", end="2024-12-31")

    @pytest.mark.parametrize(
        "invalid_date",
        [
            "2024/01/01",
            "2024-1-1",
            "2024-01-01; rm -rf /",
            "$(whoami)",
            "invalid-date",
            "",
            "2024-01-01\n--injected-flag",
            "2026-02-30",  # impossible calendar date (February 30)
            "2026-13-01",  # month out of range
        ],
    )
    def test_invalid_start_date_raises(self, invalid_date: str) -> None:
        with pytest.raises(ValueError, match="Invalid start date"):
            launch_validation_run(["trend_following"], start=invalid_date, end="2024-12-31")

    @pytest.mark.parametrize(
        "invalid_date",
        [
            "2024-13-45",  # invalid month/day
            "2024.12.31",
            "`touch pwned`",
            "2024-12-31 && echo pwned",
            "2026-02-30",  # impossible calendar date
            "2026-04-31",  # April 31 does not exist
        ],
    )
    def test_invalid_end_date_raises(self, invalid_date: str) -> None:
        with pytest.raises(ValueError, match="Invalid end date"):
            launch_validation_run(["trend_following"], start="2024-01-01", end=invalid_date)

    @pytest.mark.parametrize(
        "bad_strat",
        [
            "strat; rm -rf /",
            "strat && echo 1",
            "strat`id`",
            "$(malicious)",
            "strat|pipe",
            "strat with spaces",
            "strat/../../escape",
            "--injected-cli-flag",
        ],
    )
    def test_invalid_strategy_name_raises(self, bad_strat: str) -> None:
        with pytest.raises(ValueError, match="Invalid strategy identifier"):
            launch_validation_run([bad_strat], start="2024-01-01", end="2024-12-31")

    def test_valid_input_succeeds_with_mocked_popen(self) -> None:
        with patch("shared.orchestrator_runner.subprocess.Popen") as mock_popen, \
             patch("shared.orchestrator_runner.open", create=True):
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            handle = launch_validation_run(
                ["trend_following", "mean_reversion_pairs"],
                start="2020-01-01",
                end="2024-12-31",
            )
            assert handle.pid == 12345
            assert handle.mode == "validation"
            cmd = mock_popen.call_args[0][0]
            assert "--strategies" in cmd
            assert "trend_following,mean_reversion_pairs" in cmd
            assert "--start" in cmd
            assert "2020-01-01" in cmd
            assert "--end" in cmd
            assert "2024-12-31" in cmd


# ===========================================================================
# 1b. Command Injection & Input Validation (launch_train_meta_labelers,
#     CodeQL alert #91 -- api/_jobs.py's `POST /jobs` with
#     job_type="train_meta" passes an operator-supplied `params["signal"]`
#     straight through to this launcher's `signal` kwarg)
# ===========================================================================

class TestLaunchTrainMetaLabelersInputValidation:
    """`signal` must be exact-match allowlisted against META_LABELED_SIGNAL_IDS
    before it can reach the `subprocess.Popen` argv list -- an arbitrary
    string (shell metacharacters, an injected CLI flag, path traversal, ...)
    must always raise ValueError instead of ever being spawned."""

    @pytest.mark.parametrize(
        "bad_signal",
        [
            "timeseries_momentum; rm -rf /",
            "timeseries_momentum && echo pwned",
            "`id`",
            "$(malicious)",
            "signal|pipe",
            "--injected-cli-flag",
            "-x",
            "../../etc/passwd",
            "not_a_real_signal",
            "TIMESERIES_MOMENTUM",  # case-sensitive: must not fuzzy-match
        ],
    )
    def test_invalid_signal_raises(self, bad_signal: str) -> None:
        with pytest.raises(ValueError, match="Invalid signal identifier"):
            launch_train_meta_labelers(signal=bad_signal)

    def test_valid_signal_succeeds_with_mocked_popen(self) -> None:
        with patch("shared.orchestrator_runner.subprocess.Popen") as mock_popen, \
             patch("shared.orchestrator_runner.open", create=True):
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            handle = launch_train_meta_labelers(signal="timeseries_momentum")
            assert handle.pid == 12345
            assert handle.mode == "train_meta"
            cmd = mock_popen.call_args[0][0]
            assert cmd[cmd.index("--signal") + 1] == "timeseries_momentum"
            # Sanity: the allowlist itself must still contain what this test
            # relies on, so a future edit to META_LABELED_SIGNAL_IDS can't
            # silently make this assertion vacuous.
            assert "timeseries_momentum" in META_LABELED_SIGNAL_IDS

    def test_no_signal_succeeds_with_mocked_popen(self) -> None:
        with patch("shared.orchestrator_runner.subprocess.Popen") as mock_popen, \
             patch("shared.orchestrator_runner.open", create=True):
            mock_proc = MagicMock()
            mock_proc.pid = 12346
            mock_popen.return_value = mock_proc

            handle = launch_train_meta_labelers()
            assert handle.mode == "train_meta"
            cmd = mock_popen.call_args[0][0]
            assert "--signal" not in cmd


# ===========================================================================
# 2. Path Traversal Defense (prompt_registry/cache.py)
# ===========================================================================

class TestPromptRegistryCacheSecurity:
    """Validate that CacheManager prevents directory traversal and path injection."""

    def test_sanitize_id_normal_id(self) -> None:
        assert _sanitize_id("gravity.step_01") == "gravity_step_01"
        assert _sanitize_id("master_preprompt") == "master_preprompt"
        assert _sanitize_id("test-prompt_v2") == "test-prompt_v2"

    def test_sanitize_id_directory_traversal_attempts(self) -> None:
        assert _sanitize_id("../../etc/passwd") == "______etc_passwd"
        assert _sanitize_id(r"..\..\windows\system32") == "______windows_system32"
        assert _sanitize_id("/absolute/path") == "_absolute_path"
        assert _sanitize_id("") == "_empty_"
        assert _sanitize_id("   ") == "_empty_"

    def test_prompt_dir_and_record_path_stay_contained(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = CacheManager(cache_dir=tmpdir)
            base = Path(tmpdir).resolve()

            # Normal path
            pdir = cm._prompt_dir("gravity.step_01")
            assert pdir.resolve().is_relative_to(base)

            rec_path = cm._record_path("gravity.step_01", "1.0.0")
            assert rec_path.resolve().is_relative_to(base)

            # Malicious traversal attempts
            evil_dir = cm._prompt_dir("../../../../../../../tmp")
            assert evil_dir.resolve().is_relative_to(base)

            evil_rec = cm._record_path("../../../etc", "../../../passwd")
            assert evil_rec.resolve().is_relative_to(base)


# ===========================================================================
# 3. Exception Information Sanitization (pilots/run_status.py & prompt_registry.py)
# ===========================================================================

class TestExceptionSanitization:
    """Ensure internal paths and exception tracebacks are not leaked in API responses."""

    def test_parse_crontab_status_sanitizes_oserror(self) -> None:
        with patch("pathlib.Path.read_text", side_effect=OSError("/secret/internal/path/deploy/crontab.txt: permission denied")):
            res = parse_crontab_status()
            assert res["jobs"] == []
            assert "error" in res
            # Must be a generic sanitized message, not revealing internal path or OS error string
            assert res["error"] == "Unable to read crontab schedule"
            assert "/secret/internal/path" not in res["error"]

    def test_prompt_registry_resolution_failure_sanitizes_exception(self) -> None:
        mock_registry = MagicMock()
        mock_registry.get.side_effect = RuntimeError("Database connection string postgresql://user:secret@internal-db:5432/db failed")

        with patch("pilots.prompt_registry._get_registry_or_none", return_value=mock_registry):
            rec = get_prompt_body("master_preprompt")
            assert rec["found"] is False
            assert rec["reason"] == "Resolution failed: internal error"
            assert "secret" not in rec["reason"]
            assert "postgresql" not in rec["reason"]


class TestJobsApiValidationGating:
    """Ensure api/_jobs.py validates parameters before delegating to launchers."""

    def test_jobs_manager_rejects_malformed_validation_dates(self) -> None:
        from api._jobs import JobManager, JobType

        jm = JobManager()
        with pytest.raises(ValueError, match="Invalid date format"):
            jm.start_job(JobType.VALIDATION, {"strategies": ["trend_following"], "start": "2026-02-30", "end": "2026-12-31"})

    def test_jobs_manager_rejects_missing_strategies(self) -> None:
        from api._jobs import JobManager, JobType

        jm = JobManager()
        with pytest.raises(ValueError, match="VALIDATION job requires params"):
            jm.start_job(JobType.VALIDATION, {"strategies": [], "start": "2020-01-01", "end": "2024-12-31"})
