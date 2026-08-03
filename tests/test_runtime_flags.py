"""
tests/test_runtime_flags.py
===========================
Tests for ``runtime_flags.py`` — the READ path of the runtime settings store.

The load-bearing test in this file is
``TestByteIdenticalWithNoFile::test_apply_with_no_store_file_changes_nothing``.
Everything else is detail. ``runtime_flags.apply_overrides`` is called from the
bottom of ``settings.py``, the single highest-blast-radius file in this repo
(~146 modules import it), so the property that makes this safe to merge before
a writer exists is that with no store file on disk it changes exactly nothing —
proven by diffing a full 320-field ``model_dump()`` with and without the apply
step, not by spot-checking a few fields.

Test isolation notes (this suite runs under pytest-xdist):
  * Every test that needs a real file uses ``tmp_path`` — never a shared path,
    so parallel workers can't race.
  * No test writes to the REAL ``output/runtime_flags.json``. The tests that
    care about "no file" point at a ``tmp_path`` child that provably does not
    exist, rather than depending on the absence of the real one (which would
    make the test pass or fail based on the developer's machine state).
  * Tests that mutate the settings SINGLETON use ``monkeypatch.setattr``, which
    restores automatically. Most tests build a throwaway ``Settings()`` instead
    and never touch the singleton at all.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import runtime_flags
import settings as settings_module
import settings_keysets as ks
from settings import Settings


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "runtime_flags.py"


def write_store(path: Path, flags: dict, *, version: int = runtime_flags.SCHEMA_VERSION) -> Path:
    """Write a store file in the documented envelope shape."""
    payload = {
        "version": version,
        "flags": {k: {"value": v} for k, v in flags.items()},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def no_dotenv(monkeypatch: pytest.MonkeyPatch):
    """Pin ``.env`` parsing to empty so env-pinning tests are deterministic.

    Without this, whether a field is considered env-pinned depends on the
    developer's real ``.env`` — which differs between the operator's checkout
    (populated) and CI/a fresh worktree (absent).
    """
    monkeypatch.setattr(runtime_flags, "_dotenv_entries", lambda: {})


# ===========================================================================
# The leaf constraint — runtime_flags.py is imported BY settings.py
# ===========================================================================

class TestModuleIsADependencyFreeLeaf:
    """``settings.py`` imports this module, so it can never import back.

    A ``from settings import settings`` added here would make ``import
    settings`` fail with a circular-import error for the ENTIRE application —
    every entry point, every test, every script. Cheap to assert now,
    catastrophic to discover at runtime.
    """

    def test_module_scope_imports_are_stdlib_or_settings_keysets(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        allowed = set(sys.stdlib_module_names) | {"__future__", "settings_keysets"}

        offending = []
        for node in tree.body:  # module scope ONLY — lazy imports are checked below
            if isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root not in allowed:
                    offending.append(f"line {node.lineno}: from {node.module} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] not in allowed:
                        offending.append(f"line {node.lineno}: import {alias.name}")

        assert not offending, (
            "runtime_flags.py may only import stdlib + settings_keysets at module "
            "scope — settings.py imports IT, so any heavier module-scope import "
            "becomes a hard dependency of `import settings` for the whole "
            f"platform. Found: {offending}"
        )

    def test_module_never_imports_settings_anywhere(self):
        """Not even lazily, inside a function body — that would still cycle."""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        forbidden_roots = {"settings", "gui", "config", "db_config"}

        offending = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in forbidden_roots:
                    offending.append(f"line {node.lineno}: from {node.module} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden_roots:
                        offending.append(f"line {node.lineno}: import {alias.name}")

        assert not offending, (
            "runtime_flags.py must never import settings/gui.env_io/config at any "
            f"scope — settings.py imports it. Found: {offending}"
        )

    def test_lazy_imports_are_only_dotenv(self):
        """The one permitted non-stdlib import is python-dotenv, and it must
        stay INSIDE a function body so a broken dotenv install degrades this
        module rather than breaking `import settings`."""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        module_scope_ids = {id(n) for n in tree.body}

        nested_non_stdlib = []
        for node in ast.walk(tree):
            if id(node) in module_scope_ids:
                continue
            if isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root not in set(sys.stdlib_module_names) | {"__future__"}:
                    nested_non_stdlib.append(root)

        assert set(nested_non_stdlib) <= {"dotenv"}, (
            f"Unexpected lazy third-party import(s): {sorted(set(nested_non_stdlib))}"
        )


# ===========================================================================
# Path anchoring — the historical find_dotenv()/parent-worktree bug class
# ===========================================================================

class TestPathAnchoring:
    """The store path must be anchored to this module's OWN file location.

    ``settings.py``'s ``ENV_PATH`` comment documents a real bug in this repo:
    ``find_dotenv()`` walks UP from the calling file and, in a git worktree with
    no ``.env`` of its own, silently found a PARENT checkout's ``.env``. The
    same failure mode here would read a sibling checkout's operator state.
    """

    def test_default_store_path_is_anchored_to_the_repo_root_next_to_settings(self):
        """The assertion the task called for: this module's base path is the
        same directory ``settings.py`` anchors ``ENV_PATH`` to.

        This test CAN import settings even though runtime_flags.py cannot.
        """
        settings_dir = Path(settings_module.__file__).resolve().parent
        assert runtime_flags.DEFAULT_STORE_PATH == (
            settings_dir / "output" / runtime_flags.STORE_FILENAME
        )
        assert runtime_flags.DEFAULT_STORE_PATH.parent.parent == settings_dir
        assert runtime_flags.DEFAULT_STORE_PATH.is_absolute()

    def test_dotenv_anchor_matches_settings_env_path_exactly(self):
        """runtime_flags re-derives the ``.env`` location independently (it
        cannot import ``settings.ENV_PATH``). The two must agree, or the
        env-pinning logic would diff ``os.environ`` against a DIFFERENT file
        than the one pydantic-settings actually loaded."""
        derived = Path(runtime_flags.__file__).resolve().parent / ".env"
        assert derived == settings_module.ENV_PATH

    def test_store_path_ignores_process_cwd(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        before = runtime_flags.store_path()
        monkeypatch.chdir(tmp_path)
        assert runtime_flags.store_path() == before

    def test_explicit_path_beats_env_var_beats_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        assert runtime_flags.store_path() == runtime_flags.DEFAULT_STORE_PATH

        monkeypatch.setenv(runtime_flags.PATH_OVERRIDE_ENV_VAR, str(tmp_path / "a.json"))
        assert runtime_flags.store_path() == tmp_path / "a.json"

        # Explicit argument wins over the env var.
        assert runtime_flags.store_path(tmp_path / "b.json") == tmp_path / "b.json"


# ===========================================================================
# THE load-bearing property: no file -> byte-identical behavior
# ===========================================================================

class TestByteIdenticalWithNoFile:
    def test_apply_with_no_store_file_changes_nothing(self, tmp_path: Path):
        """Full-model proof that the apply step is a no-op with no store file.

        Compares a complete ``model_dump()`` of a settings object that has been
        through ``apply_overrides`` against one that never was. Not a spot
        check — all 320 fields.
        """
        missing = tmp_path / "definitely" / "not" / "here.json"
        assert not missing.exists()

        baseline = Settings()          # never touched by the apply step
        candidate = Settings()

        report = runtime_flags.apply_overrides(candidate, path=missing)

        assert candidate.model_dump() == baseline.model_dump()
        assert report.applied == {}
        assert report.store_present is False
        assert report.error is None

    def test_no_file_report_is_clean_not_an_error(self, tmp_path: Path):
        """A missing store is the NORMAL state of every install, not a fault.

        If absence were reported as an error, the (later) operator-facing
        surface would show a permanent scary state on a healthy machine.
        """
        report = runtime_flags.apply_overrides(Settings(), path=tmp_path / "nope.json")
        assert report.error is None
        assert report.skipped_invalid == {}
        assert report.skipped_unknown == ()

    def test_settings_singleton_matches_a_fresh_construction(self):
        """End-to-end: the real singleton — which HAS been through the
        settings.py integration point — equals a fresh, un-applied
        ``Settings()``.

        Skipped (not failed) if a real store file exists on this machine, since
        then a difference would be correct behavior rather than a regression.

        The four excluded fields are patched on the singleton by ``conftest.py``'s
        two AUTOUSE fixtures (``_no_gdelt_throttle_in_tests`` /
        ``_no_fmp_throttle_in_tests``), which zero the real ``time.sleep``-based
        rate limiters for every test in the suite. Their divergence is caused by
        the test harness, not by this module, so comparing them here would
        measure the fixtures. The remaining 316 fields still carry the property.
        """
        if runtime_flags.DEFAULT_STORE_PATH.exists():
            pytest.skip("a real runtime_flags.json exists on this machine")
        if os.environ.get(runtime_flags.PATH_OVERRIDE_ENV_VAR):
            pytest.skip("store path is overridden in this environment")

        patched_by_conftest_autouse_fixtures = {
            "GDELT_MIN_REQUEST_INTERVAL_SECONDS",
            "GDELT_RETRY_BACKOFF_SECONDS",
            "FMP_MIN_REQUEST_INTERVAL_SECONDS",
            "FMP_RETRY_BACKOFF_SECONDS",
        }
        live = settings_module.settings.model_dump()
        fresh = Settings().model_dump()
        for key in patched_by_conftest_autouse_fixtures:
            live.pop(key, None)
            fresh.pop(key, None)

        assert len(fresh) >= 300, "sanity: the exclusion list must stay tiny"
        assert live == fresh

    def test_integration_point_ran_and_reported(self):
        """settings.py must actually expose the report — proves the wiring
        executed rather than being silently swallowed by the outer try."""
        assert hasattr(settings_module, "RUNTIME_FLAGS_REPORT")
        report = settings_module.RUNTIME_FLAGS_REPORT
        assert report is not None, (
            "settings.py's runtime_flags integration raised and fell into its "
            "defensive except branch — check the logged traceback."
        )
        assert isinstance(report, runtime_flags.ApplyReport)


# ===========================================================================
# File-level failure modes — degrade, never raise (CONSTRAINT #6)
# ===========================================================================

class TestFileLevelFailuresDegrade:
    def test_corrupt_json_applies_nothing_and_does_not_raise(self, tmp_path: Path):
        bad = tmp_path / "runtime_flags.json"
        bad.write_text("{ this is not json at all ", encoding="utf-8")

        baseline = Settings()
        candidate = Settings()
        report = runtime_flags.apply_overrides(candidate, path=bad)

        assert candidate.model_dump() == baseline.model_dump()
        assert report.error is not None
        assert report.applied == {}

    def test_top_level_array_is_rejected(self, tmp_path: Path):
        bad = tmp_path / "runtime_flags.json"
        bad.write_text('["not", "an", "object"]', encoding="utf-8")
        report = runtime_flags.apply_overrides(Settings(), path=bad)
        assert report.error is not None and report.applied == {}

    def test_unknown_schema_version_is_refused_wholesale(self, tmp_path: Path):
        """A future writer bumping the version means the shape changed.
        Applying it under today's assumptions would be guessing."""
        p = tmp_path / "runtime_flags.json"
        write_store(p, {"BETA_LOOKBACK_DAYS": 999}, version=99)

        candidate = Settings()
        report = runtime_flags.apply_overrides(candidate, path=p)

        assert report.error is not None
        assert "99" in report.error
        assert candidate.BETA_LOOKBACK_DAYS != 999

    def test_flags_not_an_object_is_refused(self, tmp_path: Path):
        p = tmp_path / "runtime_flags.json"
        p.write_text(json.dumps({"version": 1, "flags": ["nope"]}), encoding="utf-8")
        report = runtime_flags.apply_overrides(Settings(), path=p)
        assert report.error is not None

    def test_unreadable_file_does_not_raise(self, tmp_path: Path):
        """A directory where a file is expected — read_text raises IsADirectoryError."""
        p = tmp_path / "runtime_flags.json"
        p.mkdir()
        report = runtime_flags.apply_overrides(Settings(), path=p)
        assert report.error is not None and report.applied == {}


# ===========================================================================
# Happy path + coercion
# ===========================================================================

class TestApplyHappyPath:
    def test_override_lands_on_the_object(self, tmp_path: Path, no_dotenv):
        p = write_store(tmp_path / "s.json", {"BETA_LOOKBACK_DAYS": 300})
        s = Settings()
        assert s.BETA_LOOKBACK_DAYS != 300

        report = runtime_flags.apply_overrides(s, path=p)

        assert s.BETA_LOOKBACK_DAYS == 300
        assert report.applied == {"BETA_LOOKBACK_DAYS": 300}
        assert report.store_present is True

    def test_string_values_are_coerced_to_the_field_type(self, tmp_path: Path, no_dotenv):
        p = write_store(
            tmp_path / "s.json",
            {"BETA_LOOKBACK_DAYS": "300", "ADVISORY_ONLY": "false"},
        )
        s = Settings()
        runtime_flags.apply_overrides(s, path=p)

        assert s.BETA_LOOKBACK_DAYS == 300 and isinstance(s.BETA_LOOKBACK_DAYS, int)
        assert s.ADVISORY_ONLY is False

    def test_report_records_the_coerced_value_not_the_raw_input(
        self, tmp_path: Path, no_dotenv
    ):
        p = write_store(tmp_path / "s.json", {"BETA_LOOKBACK_DAYS": "300"})
        report = runtime_flags.apply_overrides(Settings(), path=p)
        assert report.applied["BETA_LOOKBACK_DAYS"] == 300  # not "300"

    def test_field_validators_run_they_are_not_bypassed(self, tmp_path: Path, no_dotenv):
        """The reason this module uses ``validate_assignment`` and NOT
        ``pydantic.TypeAdapter``.

        ``ROBINHOOD_EXECUTION_MODE`` has a fail-safe ``@field_validator`` that
        collapses anything outside {off, review, live} to the inert ``off``,
        specifically so a bad/injected value can never arm live execution.
        ``TypeAdapter(str)`` would pass ``"garbage-value"`` straight through
        (verified), leaving a nonsense value where an execution-mode gate
        belongs. This test fails if anyone swaps the mechanism.
        """
        p = write_store(tmp_path / "s.json", {"ROBINHOOD_EXECUTION_MODE": "garbage-value"})
        s = Settings()
        runtime_flags.apply_overrides(s, path=p)
        assert s.ROBINHOOD_EXECUTION_MODE == "off"

    def test_field_validator_normalizes_case_and_whitespace(
        self, tmp_path: Path, no_dotenv
    ):
        p = write_store(tmp_path / "s.json", {"ROBINHOOD_EXECUTION_MODE": "  REVIEW  "})
        s = Settings()
        runtime_flags.apply_overrides(s, path=p)
        assert s.ROBINHOOD_EXECUTION_MODE == "review"

    def test_empty_flags_object_is_a_clean_no_op(self, tmp_path: Path):
        p = write_store(tmp_path / "s.json", {})
        baseline = Settings()
        candidate = Settings()
        report = runtime_flags.apply_overrides(candidate, path=p)
        assert candidate.model_dump() == baseline.model_dump()
        assert report.error is None


# ===========================================================================
# BOOTSTRAP_KEYS — mandatory, absolute exclusion
# ===========================================================================

class TestBootstrapExclusion:
    def test_bootstrap_key_in_store_is_not_applied(self, tmp_path: Path, no_dotenv):
        """The test the task called for. ``DATABASE_URL`` is a BOOTSTRAP_KEY:
        applying it live splits the process across two databases (stores
        constructed before the change keep writing to A, ones after write to
        B, with no error)."""
        assert "DATABASE_URL" in ks.BOOTSTRAP_KEYS

        p = write_store(tmp_path / "s.json", {"DATABASE_URL": "postgresql://evil/db"})
        s = Settings()
        before = s.DATABASE_URL

        report = runtime_flags.apply_overrides(s, path=p)

        assert s.DATABASE_URL == before
        assert "DATABASE_URL" not in report.applied
        assert "DATABASE_URL" in report.skipped_bootstrap

    @pytest.mark.parametrize("key", sorted(ks.BOOTSTRAP_KEYS))
    def test_every_bootstrap_key_is_refused(self, key: str, tmp_path: Path, no_dotenv):
        """Not just DATABASE_URL — all 7, so a newly added one is covered
        automatically."""
        p = write_store(tmp_path / "s.json", {key: "8888"})
        s = Settings()
        before = getattr(s, key)

        report = runtime_flags.apply_overrides(s, path=p)

        assert getattr(s, key) == before, f"{key} was applied but is bootstrap-only"
        assert key in report.skipped_bootstrap

    def test_bootstrap_key_does_not_block_other_keys_in_the_same_file(
        self, tmp_path: Path, no_dotenv
    ):
        p = write_store(
            tmp_path / "s.json",
            {"DATABASE_URL": "postgresql://evil/db", "BETA_LOOKBACK_DAYS": 300},
        )
        s = Settings()
        runtime_flags.apply_overrides(s, path=p)
        assert s.BETA_LOOKBACK_DAYS == 300


# ===========================================================================
# Precedence: real shell env > store > .env
# ===========================================================================

class TestEnvPinning:
    def test_real_shell_export_beats_the_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_dotenv
    ):
        """``SOME_FIELD=x python3 main.py`` must never be silently overridden
        by a stale JSON file."""
        monkeypatch.setenv("BETA_LOOKBACK_DAYS", "111")
        p = write_store(tmp_path / "s.json", {"BETA_LOOKBACK_DAYS": 300})

        s = Settings()  # picks up 111 from the real env
        assert s.BETA_LOOKBACK_DAYS == 111

        report = runtime_flags.apply_overrides(s, path=p)

        assert s.BETA_LOOKBACK_DAYS == 111
        assert "BETA_LOOKBACK_DAYS" in report.skipped_env_pinned
        assert "BETA_LOOKBACK_DAYS" not in report.applied

    def test_dotenv_attributable_value_does_not_pin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The subtlety this whole mechanism exists for.

        ~14 call sites run ``load_dotenv(..., override=False)``, which COPIES
        .env into os.environ. Membership alone would therefore misclassify
        every .env line as a shell export and pin it, making the store
        permanently inert for any field the operator has in .env — which is
        most of them.
        """
        monkeypatch.setattr(
            runtime_flags, "_dotenv_entries", lambda: {"BETA_LOOKBACK_DAYS": "111"}
        )
        monkeypatch.setenv("BETA_LOOKBACK_DAYS", "111")  # as load_dotenv would set it

        assert "BETA_LOOKBACK_DAYS" not in runtime_flags.real_environment_keys()

        p = write_store(tmp_path / "s.json", {"BETA_LOOKBACK_DAYS": 300})
        s = Settings()
        runtime_flags.apply_overrides(s, path=p)
        assert s.BETA_LOOKBACK_DAYS == 300

    def test_differing_value_proves_a_real_shell_export_and_pins(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """.env declares the name, but os.environ holds something else.

        ``override=False`` means load_dotenv can only ADD an absent name, never
        change one already present — so a differing value PROVES the live value
        came from a real shell export, and it must win.
        """
        monkeypatch.setattr(
            runtime_flags, "_dotenv_entries", lambda: {"BETA_LOOKBACK_DAYS": "111"}
        )
        monkeypatch.setenv("BETA_LOOKBACK_DAYS", "222")
        assert "BETA_LOOKBACK_DAYS" in runtime_flags.real_environment_keys()

    def test_name_absent_from_dotenv_is_pinned(
        self, monkeypatch: pytest.MonkeyPatch, no_dotenv
    ):
        monkeypatch.setenv("BETA_LOOKBACK_DAYS", "111")
        assert "BETA_LOOKBACK_DAYS" in runtime_flags.real_environment_keys()

    def test_pinning_is_case_insensitive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_dotenv
    ):
        """``Settings.model_config`` sets ``case_sensitive=False``, so a
        lowercase shell export is a real override of the field and must pin."""
        monkeypatch.setenv("beta_lookback_days", "111")
        p = write_store(tmp_path / "s.json", {"BETA_LOOKBACK_DAYS": 300})
        s = Settings()
        report = runtime_flags.apply_overrides(s, path=p)
        assert "BETA_LOOKBACK_DAYS" in report.skipped_env_pinned
        assert s.BETA_LOOKBACK_DAYS != 300

    def test_unset_name_is_not_pinned(self, monkeypatch: pytest.MonkeyPatch, no_dotenv):
        monkeypatch.delenv("BETA_LOOKBACK_DAYS", raising=False)
        assert "BETA_LOOKBACK_DAYS" not in runtime_flags.real_environment_keys()


# ===========================================================================
# Dead-letter per key — one bad entry never poisons the file
# ===========================================================================

class TestPerKeyDeadLetter:
    def test_unknown_field_name_is_skipped_alone(self, tmp_path: Path, no_dotenv):
        p = write_store(
            tmp_path / "s.json",
            {"NOT_A_REAL_SETTING": 1, "BETA_LOOKBACK_DAYS": 300},
        )
        s = Settings()
        report = runtime_flags.apply_overrides(s, path=p)

        assert s.BETA_LOOKBACK_DAYS == 300
        assert report.skipped_unknown == ("NOT_A_REAL_SETTING",)
        assert not hasattr(s, "NOT_A_REAL_SETTING")

    def test_bad_value_is_skipped_alone_and_leaves_the_field_untouched(
        self, tmp_path: Path, no_dotenv
    ):
        p = write_store(
            tmp_path / "s.json",
            {"BETA_LOOKBACK_DAYS": "not-an-integer", "FORECAST_PROPHET_WEIGHT": 0.35},
        )
        s = Settings()
        before = s.BETA_LOOKBACK_DAYS

        report = runtime_flags.apply_overrides(s, path=p)

        assert s.BETA_LOOKBACK_DAYS == before
        assert s.FORECAST_PROPHET_WEIGHT == 0.35
        assert "BETA_LOOKBACK_DAYS" in report.skipped_invalid

    def test_bare_scalar_entry_is_skipped_alone(self, tmp_path: Path, no_dotenv):
        """The envelope is required, not optional — a dict-typed field's real
        value could otherwise be mistaken for an envelope."""
        p = tmp_path / "s.json"
        p.write_text(
            json.dumps(
                {
                    "version": 1,
                    "flags": {
                        "FORECAST_PROPHET_WEIGHT": 0.9,          # bare — rejected
                        "BETA_LOOKBACK_DAYS": {"value": 300},    # enveloped — applied
                    },
                }
            ),
            encoding="utf-8",
        )
        s = Settings()
        before = s.FORECAST_PROPHET_WEIGHT

        runtime_flags.apply_overrides(s, path=p)

        assert s.FORECAST_PROPHET_WEIGHT == before
        assert s.BETA_LOOKBACK_DAYS == 300

    def test_writer_metadata_siblings_are_ignored_not_rejected(
        self, tmp_path: Path, no_dotenv
    ):
        """The not-yet-built writer will add audit metadata; extra keys beside
        ``value`` must not break the reader."""
        p = tmp_path / "s.json"
        p.write_text(
            json.dumps(
                {
                    "version": 1,
                    "flags": {
                        "BETA_LOOKBACK_DAYS": {
                            "value": 300,
                            "updated_at": "2026-08-03T12:00:00+00:00",
                            "updated_by": "pilots_api",
                            "reason": "widen the beta window",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        s = Settings()
        runtime_flags.apply_overrides(s, path=p)
        assert s.BETA_LOOKBACK_DAYS == 300


# ===========================================================================
# Secret hygiene — a store entry may hold a credential
# ===========================================================================

class TestNeverLogsStoredValues:
    def test_rejection_reason_never_echoes_the_rejected_value(
        self, tmp_path: Path, no_dotenv, caplog
    ):
        """``str(ValidationError)`` embeds ``input_value=`` (verified) and would
        print a rejected credential into the log. Only pydantic's structured
        ``errors()[0]['msg']`` is used."""
        secret = "sup3r-s3cr3t-token-value"
        p = write_store(tmp_path / "s.json", {"BETA_LOOKBACK_DAYS": secret})

        with caplog.at_level("DEBUG"):
            report = runtime_flags.apply_overrides(Settings(), path=p)

        assert secret not in report.skipped_invalid["BETA_LOOKBACK_DAYS"]
        assert secret not in caplog.text

    def test_applied_values_are_not_logged(self, tmp_path: Path, no_dotenv, caplog):
        marker = "value-that-must-not-be-logged"
        p = write_store(tmp_path / "s.json", {"ALERT_WEBHOOK_URL": marker})

        with caplog.at_level("DEBUG"):
            runtime_flags.apply_overrides(Settings(), path=p)

        assert marker not in caplog.text


# ===========================================================================
# End-to-end: a REAL `import settings` in a fresh process
# ===========================================================================

class TestSettingsPyIntegration:
    """Proves the whole mechanism, not just that the internals are individually
    correct: a real ``import settings`` in a fresh interpreter, with a real
    store file on disk, produces an overridden singleton."""

    def _run(self, tmp_path: Path, store: Path, expr: str) -> str:
        env = dict(os.environ)
        env[runtime_flags.PATH_OVERRIDE_ENV_VAR] = str(store)
        # Must not be env-pinned in the child, or the store would (correctly)
        # be skipped and this test would be measuring the wrong thing.
        env.pop("BETA_LOOKBACK_DAYS", None)
        env.pop("beta_lookback_days", None)
        proc = subprocess.run(
            [sys.executable, "-c", f"import settings; print({expr})"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, (
            f"`import settings` FAILED in a subprocess.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        return proc.stdout.strip()

    def test_real_import_settings_applies_the_override(self, tmp_path: Path):
        store = write_store(tmp_path / "runtime_flags.json", {"BETA_LOOKBACK_DAYS": 377})
        out = self._run(tmp_path, store, "settings.settings.BETA_LOOKBACK_DAYS")
        assert out == "377"

    def test_real_import_settings_survives_a_corrupt_store(self, tmp_path: Path):
        """The single most important safety property of the integration point:
        a corrupt file must never stop `import settings`, because every entry
        point in this application would break."""
        store = tmp_path / "runtime_flags.json"
        store.write_text("}{ not json", encoding="utf-8")
        out = self._run(tmp_path, store, "settings.RUNTIME_FLAGS_REPORT.error is not None")
        assert out == "True"

    def test_real_import_settings_refuses_a_bootstrap_key(self, tmp_path: Path):
        store = write_store(
            tmp_path / "runtime_flags.json",
            {"DATABASE_URL": "postgresql://should-never-apply/db"},
        )
        out = self._run(
            tmp_path, store, "settings.settings.DATABASE_URL"
        )
        assert "should-never-apply" not in out

    def test_real_import_settings_with_no_file_reports_absent(self, tmp_path: Path):
        missing = tmp_path / "runtime_flags.json"
        assert not missing.exists()
        out = self._run(tmp_path, missing, "settings.RUNTIME_FLAGS_REPORT.store_present")
        assert out == "False"
