"""
tests/test_runtime_flags_writer.py
==================================
Tests for ``runtime_flags_writer.py`` — the WRITE path of the runtime settings
store.

Companion to ``tests/test_runtime_flags.py`` (the READ path). Where that file's
load-bearing property was "with no store file, nothing changes", this file's is
the opposite: **a write must change exactly the things it claims to change, and
nothing else** — not the live singleton on a refusal, not another key's stored
provenance on a success, and never a credential into a plaintext file or an
audit log.

Test isolation notes (this suite runs under pytest-xdist):
  * Every test passes an explicit ``path=tmp_path/...``. No test can reach the
    operator's real ``output/runtime_flags.json``, and because the audit log is
    resolved as a SIBLING of the store, no test can reach the real audit trail
    either.
  * Tests that need a live singleton monkeypatch ``settings.settings`` to a
    throwaway ``Settings()``; the real singleton is never mutated in-process.
  * The claims that are only meaningful against the REAL singleton and a REAL
    ``import settings`` are proven in a fresh subprocess interpreter
    (``TestFreshInterpreter``) — this pytest process has ~1300 other tests free
    to have patched the singleton, so an in-process assertion there would be
    measuring test pollution.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import pytest

import runtime_flags
import runtime_flags_writer as writer
import settings as settings_module
import settings_keysets as ks
from gui import env_io
from settings import Settings


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "runtime_flags_writer.py"

# A plain int field: not secret, not bootstrap, absent from os.environ, and
# cheap to reason about. Used as the default subject throughout.
FIELD = "BETA_LOOKBACK_DAYS"
OTHER_FIELD = "FORECAST_PROPHET_WEIGHT"

# Distinct non-secret, non-bootstrap fields for the concurrency test.
CONCURRENT_FIELDS = (
    "BETA_LOOKBACK_DAYS",
    "MACRO_REFRESH_HOURS",
    "BARS_BACKFILL_DAYS",
    "FUNDAMENTALS_REFRESH_DAYS",
    "PROGRESS_POLL_SECONDS",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A store path inside ``tmp_path`` that provably does not exist yet."""
    return tmp_path / "runtime_flags.json"


@pytest.fixture
def no_dotenv(monkeypatch: pytest.MonkeyPatch):
    """Pin ``.env`` parsing to empty so env-pinning is deterministic.

    Same fixture as ``tests/test_runtime_flags.py``: without it, whether a field
    counts as env-pinned depends on the developer's real ``.env``, which differs
    between the operator's checkout (populated) and CI (absent).

    Patching ``_dotenv_entries`` alone is not sufficient on a machine with a
    real, populated ``.env``: another test module's own import-time
    ``load_dotenv(ENV_PATH, override=False)`` call (~14 call sites across this
    codebase) copies ``.env`` into real ``os.environ`` earlier in this same
    pytest session, and that mutation persists independent of this fixture.
    With ``_dotenv_entries`` patched to ``{}``, any such name already present
    in ``os.environ`` then looks like a genuine shell export to
    ``real_environment_keys()`` and gets pinned. The ``live`` fixture already
    clears the FIELD/OTHER_FIELD/CONCURRENT_FIELDS probes it uses;
    ``ROBINHOOD_EXECUTION_MODE`` is the one additional probe field used
    directly by this file's ``TestInvalidValue`` tests, cleared here too so
    they are deterministic regardless of this machine's real .env.
    """
    monkeypatch.setattr(runtime_flags, "_dotenv_entries", lambda: {})
    monkeypatch.delenv("ROBINHOOD_EXECUTION_MODE", raising=False)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Swap ``settings.settings`` for a throwaway ``Settings()``.

    The writer resolves the singleton through ``settings_module.settings`` at
    call time precisely so this works — the real singleton is never touched, and
    ``monkeypatch`` restores the module attribute automatically.
    """
    for name in {FIELD, OTHER_FIELD, *CONCURRENT_FIELDS}:
        monkeypatch.delenv(name, raising=False)
    throwaway = Settings()
    monkeypatch.setattr(settings_module, "settings", throwaway)
    return throwaway


def read_store(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def audit_records(path: Path) -> list[dict]:
    """Every audit record written for the store at ``path``."""
    audit = writer.audit_path(path)
    if not audit.exists():
        return []
    return [
        json.loads(line)
        for line in audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_in_fresh_interpreter(
    code: str, store_path: Path, *, scrub_env: tuple[str, ...] = ()
) -> str:
    """Run ``code`` in a pristine interpreter pointed at ``store_path``.

    Mirrors ``tests/test_runtime_flags.py``'s helper of the same name, and for
    the same reason: what is worth proving is a property of a REAL ``import
    settings`` against the REAL singleton, which this pytest process cannot
    offer deterministically.
    """
    env = dict(os.environ)
    env[runtime_flags.PATH_OVERRIDE_ENV_VAR] = str(store_path)
    for name in scrub_env:
        env.pop(name, None)
        env.pop(name.lower(), None)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"fresh interpreter FAILED.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc.stdout.strip()


# ===========================================================================
# Module wiring — the writer must stay OFF settings.py's import path
# ===========================================================================


class TestModuleWiring:
    """``runtime_flags.py`` is a stdlib-only leaf because ``settings.py``
    imports it. This module is the opposite — it imports ``settings`` and
    ``env_io`` — which is only safe as long as nothing on ``settings.py``'s
    own import path imports it back.

    ``env_io`` (formerly ``gui.env_io`` -- relocated to the repo root, F13
    in docs/module_efficiency_redundancy_audit.md) is the module name
    asserted below; ``gui.env_io`` now only re-exports it via a shim for
    the frozen Command Center's own internal imports and is no longer
    what this module itself imports.
    """

    @pytest.mark.parametrize("module", ["settings.py", "runtime_flags.py", "settings_keysets.py"])
    def test_the_import_leaf_chain_never_imports_the_writer(self, module: str):
        """A ``import runtime_flags_writer`` anywhere in this chain is a
        circular import that would break ``import settings`` for all ~146
        dependent modules — i.e. every entry point in the platform."""
        source = (REPO_ROOT / module).read_text(encoding="utf-8")
        assert "runtime_flags_writer" not in source, (
            f"{module} references runtime_flags_writer. That module imports "
            f"settings and env_io; importing it from here is a circular "
            f"import that breaks `import settings` platform-wide."
        )

    def test_writer_really_does_import_the_secret_keyset(self):
        """The whole reason this module exists as a separate, non-leaf file:
        ``runtime_flags.py`` cannot import ``env_io``, so the SECRET_KEYS
        refusal had to live here."""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert "env_io" in imported
        assert "settings" in imported
        assert "runtime_flags" in imported


# ===========================================================================
# Gate 1 — secrets
# ===========================================================================


class TestSecretRefusal:
    """CONSTRAINT #3. ``runtime_flags.py``'s docstring names this the one gap it
    could not close; closing it is why this module is not a leaf."""

    SENTINEL = "hunter2-SENTINEL-must-never-be-persisted"

    def test_secret_field_is_refused(self, store: Path, live: Settings):
        result = writer.write_override(
            "ROBINHOOD_PASSWORD", self.SENTINEL, actor="test", path=store
        )

        assert result.ok is False
        assert result.persisted is False
        assert result.applies == writer.APPLIES_REFUSED
        assert result.applied_value is None
        assert result.reason == (
            "secret fields can never be stored in the runtime settings store"
        )

    def test_the_secret_never_reaches_the_store_file(
        self, store: Path, live: Settings
    ):
        writer.write_override(
            "ROBINHOOD_PASSWORD", self.SENTINEL, actor="test", path=store
        )
        assert not store.exists(), "a refused write must not create the store"

    def test_the_secret_never_reaches_the_audit_log(
        self, store: Path, live: Settings
    ):
        writer.write_override(
            "ROBINHOOD_PASSWORD", self.SENTINEL, actor="test", path=store
        )
        audit = writer.audit_path(store)
        assert audit.exists(), "the refusal itself must still be audited"
        raw = audit.read_text(encoding="utf-8")
        assert self.SENTINEL not in raw
        assert "hunter2" not in raw

        (record,) = audit_records(store)
        assert record["key"] == "ROBINHOOD_PASSWORD"
        assert record["ok"] is False
        assert record["applies"] == writer.APPLIES_REFUSED
        assert record["actor"] == "test"

    def test_the_secret_gate_runs_before_the_field_existence_gate(
        self, store: Path, live: Settings
    ):
        """38 of the 40 SECRET_KEYS are real ``Settings`` fields. If the field
        check ran first this test would still pass by accident for a
        non-field name, so it deliberately uses one that IS a field: the
        refusal has to be attributable to secrecy."""
        assert "ROBINHOOD_PASSWORD" in Settings.model_fields
        result = writer.write_override(
            "ROBINHOOD_PASSWORD", self.SENTINEL, path=store
        )
        assert "secret" in result.reason
        assert "not a Settings field" not in result.reason

    @pytest.mark.parametrize(
        "key", sorted(k for k in env_io.SECRET_KEYS if k in Settings.model_fields)
    )
    def test_every_secret_settings_field_is_refused(
        self, key: str, store: Path, live: Settings
    ):
        """All 38, so a newly added credential is covered automatically."""
        result = writer.write_override(key, "x", path=store)
        assert result.ok is False
        assert result.applies == writer.APPLIES_REFUSED
        assert not store.exists()


# ===========================================================================
# Gate 2 — bootstrap keys
# ===========================================================================


class TestBootstrapRefusal:
    # DATABASE_URL and MCP_DATABASE_URL_RO are in BOTH BOOTSTRAP_KEYS and
    # SECRET_KEYS. Gate 1 runs first, so those two refuse as secrets — see
    # test_a_bootstrap_key_that_is_also_secret_refuses_as_a_secret.
    BOOTSTRAP_ONLY = sorted(k for k in ks.BOOTSTRAP_KEYS if k not in env_io.SECRET_KEYS)

    def test_bootstrap_key_is_refused_with_its_own_reason(
        self, store: Path, live: Settings
    ):
        result = writer.write_override("ORCHESTRATOR_API_PORT", 9999, path=store)
        assert result.ok is False
        assert result.persisted is False
        assert result.applies == writer.APPLIES_REFUSED
        # The per-field reason, not a generic message — an operator needs to
        # know WHY this particular field can never be stored.
        assert result.reason == ks.BOOTSTRAP_KEY_REASONS["ORCHESTRATOR_API_PORT"]
        assert "Bound once, read live" in result.reason
        assert not store.exists()

    @pytest.mark.parametrize("key", BOOTSTRAP_ONLY)
    def test_every_bootstrap_only_key_is_refused(
        self, key: str, store: Path, live: Settings
    ):
        result = writer.write_override(key, "8888", path=store)
        assert result.ok is False
        assert result.reason == ks.BOOTSTRAP_KEY_REASONS[key]
        assert not store.exists()

    @pytest.mark.parametrize(
        "key", sorted(k for k in ks.BOOTSTRAP_KEYS if k in env_io.SECRET_KEYS)
    )
    def test_a_bootstrap_key_that_is_also_secret_refuses_as_a_secret(
        self, key: str, store: Path, live: Settings
    ):
        """The two key sets overlap on the two database DSNs, and gate ordering
        decides which reason an operator sees.

        Secret-first is the right way round: a DSN can embed credentials, and
        "this is a secret" is both the stronger statement and the one that must
        not read as something to work around. ``settings_keysets.py``'s own
        ``DATABASE_URL`` reason says so ("SECRET too (may embed credentials),
        but that is a separate mechanism"). Refused either way — this pins
        WHICH refusal, so a future reordering of the gates is a deliberate act.
        """
        result = writer.write_override(key, "postgresql://evil/db", path=store)
        assert result.ok is False
        assert result.reason == (
            "secret fields can never be stored in the runtime settings store"
        )
        assert not store.exists()

    def test_the_overlap_is_exactly_the_two_database_dsns(self):
        """Guards the parametrization above: if a third key joins both sets,
        this fails and someone has to decide deliberately which gate should
        own it."""
        assert sorted(k for k in ks.BOOTSTRAP_KEYS if k in env_io.SECRET_KEYS) == [
            "DATABASE_URL",
            "MCP_DATABASE_URL_RO",
        ]

    def test_a_bootstrap_refusal_does_not_disturb_an_existing_store(
        self, store: Path, live: Settings, no_dotenv
    ):
        writer.write_override(FIELD, 300, actor="first", path=store)
        before = store.read_text(encoding="utf-8")

        writer.write_override("OUTPUT_DIR", "/tmp/evil", path=store)

        assert store.read_text(encoding="utf-8") == before


# ===========================================================================
# Gate 3 — unknown field names
# ===========================================================================


class TestUnknownField:
    def test_unknown_name_is_refused(self, store: Path, live: Settings):
        result = writer.write_override("NOT_A_REAL_FIELD", 1, path=store)
        assert result.ok is False
        assert result.persisted is False
        assert result.applies == writer.APPLIES_REFUSED
        assert "not a Settings field name" in result.reason
        assert not store.exists()

    def test_a_typo_is_not_silently_stored(self, store: Path, live: Settings):
        """``Settings`` sets ``extra="ignore"``, so without this gate a typo
        would be stored forever and skipped forever."""
        writer.write_override("BETA_LOOKBACK_DAYZ", 300, path=store)
        assert not store.exists()


# ===========================================================================
# Gate 4 — validation
# ===========================================================================


class TestInvalidValue:
    def test_bad_value_is_refused_and_the_live_value_is_unchanged(
        self, store: Path, live: Settings, no_dotenv
    ):
        before = live.BETA_LOOKBACK_DAYS

        result = writer.write_override(FIELD, "not-a-number", path=store)

        assert result.ok is False
        assert result.persisted is False
        assert result.applies == writer.APPLIES_REFUSED
        assert live.BETA_LOOKBACK_DAYS == before
        assert not store.exists()

    def test_the_whole_settings_object_is_untouched_by_a_refusal(
        self, store: Path, live: Settings, no_dotenv
    ):
        """Validating on a ``model_copy()`` rather than the live singleton is
        what makes a refusal leave this process byte-identical."""
        before = live.model_dump()
        writer.write_override(FIELD, "not-a-number", path=store)
        assert live.model_dump() == before

    def test_the_reason_is_pydantics_message_and_never_echoes_the_input(
        self, store: Path, live: Settings, no_dotenv
    ):
        secretish = "sk-live-DO-NOT-LEAK-0123456789"
        result = writer.write_override(FIELD, secretish, path=store)

        assert result.reason == (
            "Input should be a valid integer, unable to parse string as an integer"
        )
        assert secretish not in result.reason

    def test_str_of_the_exception_would_have_leaked_it(self, live: Settings):
        """The justification for the ``errors()[0]["msg"]`` rule. If pydantic
        ever stopped embedding ``input_value=`` this test would fail and the
        rule could be revisited — until then it is load-bearing."""
        secretish = "sk-live-DO-NOT-LEAK-0123456789"
        probe = live.model_copy()
        with pytest.raises(Exception) as excinfo:
            Settings.__pydantic_validator__.validate_assignment(
                probe, FIELD, secretish
            )
        assert secretish in str(excinfo.value)
        assert secretish not in excinfo.value.errors()[0]["msg"]

    def test_a_rejected_value_never_reaches_the_audit_log(
        self, store: Path, live: Settings, no_dotenv
    ):
        secretish = "sk-live-DO-NOT-LEAK-0123456789"
        writer.write_override(FIELD, secretish, path=store)
        raw = writer.audit_path(store).read_text(encoding="utf-8")
        assert secretish not in raw

    def test_field_validators_run_they_are_not_bypassed(
        self, store: Path, live: Settings, no_dotenv
    ):
        """``ROBINHOOD_EXECUTION_MODE`` has a fail-safe ``@field_validator``
        collapsing anything outside {off, review, live} to the inert ``off``,
        specifically so a bad value can never arm live execution.
        ``pydantic.TypeAdapter`` would pass ``"garbage"`` straight through.
        This test fails if anyone swaps the mechanism."""
        result = writer.write_override(
            "ROBINHOOD_EXECUTION_MODE", "garbage-value", path=store
        )
        assert result.ok is True
        assert result.applied_value == "off"
        assert read_store(store)["flags"]["ROBINHOOD_EXECUTION_MODE"]["value"] == "off"

    def test_the_stored_value_is_the_normalized_one(
        self, store: Path, live: Settings, no_dotenv
    ):
        result = writer.write_override(
            "ROBINHOOD_EXECUTION_MODE", "  REVIEW  ", path=store
        )
        assert result.applied_value == "review"
        assert read_store(store)["flags"]["ROBINHOOD_EXECUTION_MODE"]["value"] == "review"


# ===========================================================================
# The happy path
# ===========================================================================


class TestSuccessfulWrite:
    def test_result_shape(self, store: Path, live: Settings, no_dotenv):
        result = writer.write_override(FIELD, 300, actor="pilots_api", path=store)

        assert result.key == FIELD
        assert result.ok is True
        assert result.persisted is True
        assert result.applies == writer.APPLIES_IMMEDIATELY
        assert result.applied_value == 300
        assert result.reason is None

    def test_file_uses_the_documented_envelope(
        self, store: Path, live: Settings, no_dotenv
    ):
        writer.write_override(FIELD, 300, actor="pilots_api", path=store)

        payload = read_store(store)
        assert payload["version"] == runtime_flags.SCHEMA_VERSION
        entry = payload["flags"][FIELD]
        assert set(entry) == {"value", "updated_at", "updated_by"}
        assert entry["value"] == 300
        assert entry["updated_by"] == "pilots_api"
        # ISO 8601, timezone-aware UTC.
        stamp = datetime.fromisoformat(entry["updated_at"])
        assert stamp.tzinfo is not None
        assert stamp.utcoffset().total_seconds() == 0

    def test_the_read_path_can_load_it_back(
        self, store: Path, live: Settings, no_dotenv
    ):
        """The two halves have to agree about the file format."""
        writer.write_override(FIELD, 300, path=store)

        flags, error = runtime_flags.load_store(store)
        assert error is None
        assert flags == {FIELD: 300}

    def test_it_is_live_on_the_settings_object_with_no_restart(
        self, store: Path, live: Settings, no_dotenv
    ):
        assert live.BETA_LOOKBACK_DAYS != 300
        writer.write_override(FIELD, 300, path=store)
        assert live.BETA_LOOKBACK_DAYS == 300
        assert settings_module.settings.BETA_LOOKBACK_DAYS == 300

    def test_a_string_is_coerced_and_the_COERCED_value_is_stored(
        self, store: Path, live: Settings, no_dotenv
    ):
        """Storing the raw ``"300"`` would make the file round-trip differently
        from what actually took effect."""
        result = writer.write_override(FIELD, "300", path=store)

        assert result.applied_value == 300
        assert isinstance(result.applied_value, int)
        stored = read_store(store)["flags"][FIELD]["value"]
        assert stored == 300 and isinstance(stored, int)
        assert live.BETA_LOOKBACK_DAYS == 300

    def test_other_keys_keep_their_value_AND_their_provenance(
        self, store: Path, live: Settings, no_dotenv
    ):
        """The merge must preserve each untouched entry's whole envelope.

        Rebuilding the file from ``runtime_flags.load_store``'s output would
        pass a naive "the other value is still there" check while silently
        restamping ``updated_at``/``updated_by`` — destroying the provenance of
        an override this call was never asked to touch.
        """
        writer.write_override(OTHER_FIELD, 0.4, actor="operator", path=store)
        # Give the untouched entry a distinctive envelope, including a sibling
        # key the writer has no reason to know about.
        payload = read_store(store)
        payload["flags"][OTHER_FIELD]["updated_at"] = "2020-01-01T00:00:00+00:00"
        payload["flags"][OTHER_FIELD]["note"] = "keep-me-verbatim"
        store.write_text(json.dumps(payload), encoding="utf-8")
        untouched_before = dict(payload["flags"][OTHER_FIELD])

        writer.write_override(FIELD, 300, actor="pilots_api", path=store)

        after = read_store(store)["flags"]
        assert after[OTHER_FIELD] == untouched_before
        assert after[FIELD]["value"] == 300

    def test_exactly_one_audit_line_with_no_value_in_it(
        self, store: Path, live: Settings, no_dotenv
    ):
        writer.write_override(FIELD, 4242, actor="pilots_api", path=store)

        records = audit_records(store)
        assert len(records) == 1
        record = records[0]
        # A closed key set: a new field added to the audit record has to be a
        # deliberate change, not something that quietly starts carrying a value.
        assert set(record) == {
            "ts",
            "action",
            "key",
            "actor",
            "ok",
            "persisted",
            "applies",
        }
        assert record["action"] == "write"
        assert record["key"] == FIELD
        assert record["actor"] == "pilots_api"
        assert record["ok"] is True
        assert record["persisted"] is True
        assert record["applies"] == writer.APPLIES_IMMEDIATELY
        # No field of the record is (or contains) the written value.
        assert 4242 not in record.values()
        assert "4242" not in json.dumps(
            {k: v for k, v in record.items() if k != "ts"}
        )

    def test_the_audit_log_lands_beside_the_store_not_in_output(
        self, store: Path, live: Settings, no_dotenv
    ):
        """Why redirecting the store in a test is enough to isolate everything."""
        writer.write_override(FIELD, 300, path=store)
        assert writer.audit_path(store).parent == store.resolve().parent
        assert writer.audit_path(store).name == "runtime_flags_audit.jsonl"

    def test_two_writes_to_the_same_key_overwrite_rather_than_accumulate(
        self, store: Path, live: Settings, no_dotenv
    ):
        writer.write_override(FIELD, 300, actor="first", path=store)
        writer.write_override(FIELD, 301, actor="second", path=store)

        entry = read_store(store)["flags"][FIELD]
        assert entry["value"] == 301
        assert entry["updated_by"] == "second"
        assert live.BETA_LOOKBACK_DAYS == 301
        assert len(audit_records(store)) == 2


# ===========================================================================
# Precedence — a real shell export still wins
# ===========================================================================


class TestEnvPinned:
    def test_write_persists_but_does_not_move_the_live_value(
        self, store: Path, monkeypatch: pytest.MonkeyPatch, no_dotenv
    ):
        monkeypatch.setenv(FIELD, "111")
        throwaway = Settings()  # picks 111 up from the real environment
        monkeypatch.setattr(settings_module, "settings", throwaway)
        assert throwaway.BETA_LOOKBACK_DAYS == 111

        result = writer.write_override(FIELD, 300, actor="pilots_api", path=store)

        # Persisted: durable for a future process that is not pinned.
        assert result.persisted is True
        assert read_store(store)["flags"][FIELD]["value"] == 300
        # But this process did NOT move, and says so.
        assert result.ok is True
        assert result.applies == writer.APPLIES_ENV_PINNED
        assert throwaway.BETA_LOOKBACK_DAYS == 111

    def test_applied_value_reports_the_env_value_that_actually_won(
        self, store: Path, monkeypatch: pytest.MonkeyPatch, no_dotenv
    ):
        """Reporting the written value here would make ``applied_value`` a lie
        — the whole point of ``env_pinned`` is that the caller can see the gap.
        """
        monkeypatch.setenv(FIELD, "111")
        monkeypatch.setattr(settings_module, "settings", Settings())

        result = writer.write_override(FIELD, 300, path=store)

        assert result.applied_value == 111

    def test_the_env_pinned_outcome_is_audited(
        self, store: Path, monkeypatch: pytest.MonkeyPatch, no_dotenv
    ):
        monkeypatch.setenv(FIELD, "111")
        monkeypatch.setattr(settings_module, "settings", Settings())
        writer.write_override(FIELD, 300, path=store)

        (record,) = audit_records(store)
        assert record["ok"] is True
        assert record["persisted"] is True
        assert record["applies"] == writer.APPLIES_ENV_PINNED


# ===========================================================================
# delete_override
# ===========================================================================


class TestDeleteOverride:
    def test_delete_reverts_the_live_value_and_removes_the_entry(
        self, store: Path, live: Settings, no_dotenv
    ):
        baseline = Settings().BETA_LOOKBACK_DAYS
        writer.write_override(FIELD, 300, path=store)
        assert live.BETA_LOOKBACK_DAYS == 300

        result = writer.delete_override(FIELD, actor="operator", path=store)

        assert result.ok is True
        assert result.persisted is True
        assert result.applies == writer.APPLIES_IMMEDIATELY
        assert result.reason is None
        assert result.applied_value == baseline
        assert live.BETA_LOOKBACK_DAYS == baseline
        assert FIELD not in read_store(store)["flags"]

    def test_delete_leaves_other_overrides_alone(
        self, store: Path, live: Settings, no_dotenv
    ):
        writer.write_override(FIELD, 300, path=store)
        writer.write_override(OTHER_FIELD, 0.4, actor="operator", path=store)

        writer.delete_override(FIELD, path=store)

        flags = read_store(store)["flags"]
        assert FIELD not in flags
        assert flags[OTHER_FIELD]["value"] == 0.4
        assert flags[OTHER_FIELD]["updated_by"] == "operator"
        assert live.FORECAST_PROPHET_WEIGHT == 0.4

    def test_deleting_an_absent_key_is_a_clean_no_op(
        self, store: Path, live: Settings, no_dotenv
    ):
        """Deleting something that was never there is not an error."""
        writer.write_override(OTHER_FIELD, 0.4, path=store)
        before = store.read_text(encoding="utf-8")

        result = writer.delete_override(FIELD, path=store)

        assert result.ok is True
        assert result.persisted is False
        assert result.reason is None
        assert store.read_text(encoding="utf-8") == before

    def test_deleting_from_a_store_that_does_not_exist_is_a_clean_no_op(
        self, store: Path, live: Settings, no_dotenv
    ):
        result = writer.delete_override(FIELD, path=store)
        assert result.ok is True
        assert result.persisted is False
        assert not store.exists()

    def test_delete_is_audited(self, store: Path, live: Settings, no_dotenv):
        writer.write_override(FIELD, 300, path=store)
        writer.delete_override(FIELD, actor="operator", path=store)

        records = audit_records(store)
        assert len(records) == 2
        assert records[1]["action"] == "delete"
        assert records[1]["key"] == FIELD
        assert records[1]["actor"] == "operator"
        assert records[1]["ok"] is True
        assert records[1]["persisted"] is True

    def test_a_no_op_delete_is_still_audited(
        self, store: Path, live: Settings, no_dotenv
    ):
        writer.delete_override(FIELD, actor="operator", path=store)
        (record,) = audit_records(store)
        assert record["action"] == "delete"
        assert record["persisted"] is False

    def test_delete_removes_a_hand_added_secret_entry(
        self, store: Path, live: Settings, no_dotenv
    ):
        """Deletion deliberately applies no classification gates: it can only
        ever reduce what the store overrides, so refusing here would strand a
        hand-added entry that the READ path would keep applying."""
        store.write_text(
            json.dumps(
                {
                    "version": runtime_flags.SCHEMA_VERSION,
                    "flags": {"ROBINHOOD_PASSWORD": {"value": "leaked"}},
                }
            ),
            encoding="utf-8",
        )

        result = writer.delete_override("ROBINHOOD_PASSWORD", path=store)

        assert result.ok is True
        assert result.persisted is True
        assert read_store(store)["flags"] == {}

    def test_delete_of_an_env_pinned_key_reports_env_pinned(
        self, store: Path, monkeypatch: pytest.MonkeyPatch, no_dotenv
    ):
        monkeypatch.setenv(FIELD, "111")
        monkeypatch.setattr(settings_module, "settings", Settings())
        writer.write_override(FIELD, 300, path=store)

        result = writer.delete_override(FIELD, path=store)

        assert result.ok is True
        assert result.persisted is True
        assert result.applies == writer.APPLIES_ENV_PINNED
        assert result.applied_value == 111
        assert FIELD not in read_store(store)["flags"]


# ===========================================================================
# Concurrency
# ===========================================================================


class TestConcurrentWriters:
    """Scoped rigor: an in-process lock plus a late read. See the module
    docstring's "what is guaranteed, and what is not"."""

    def test_sequential_writes_preserve_each_others_keys(
        self, store: Path, live: Settings, no_dotenv
    ):
        for field in CONCURRENT_FIELDS:
            writer.write_override(field, 7, path=store)

        flags = read_store(store)["flags"]
        assert set(flags) == set(CONCURRENT_FIELDS)
        assert all(entry["value"] == 7 for entry in flags.values())

    def test_concurrent_threads_writing_different_keys_all_land(
        self, store: Path, live: Settings, no_dotenv
    ):
        """The realistic race: FastAPI runs ``def`` handlers in a threadpool, so
        two near-simultaneous PUTs are genuinely concurrent threads in one
        process. Without ``_WRITE_LOCK`` this loses keys — each thread's
        read-modify-write would be built on a stale view of the file."""
        barrier = threading.Barrier(len(CONCURRENT_FIELDS))
        results: dict[str, writer.WriteResult] = {}
        errors: list[BaseException] = []

        def worker(field: str) -> None:
            try:
                barrier.wait(timeout=30)  # maximize overlap
                results[field] = writer.write_override(field, 7, path=store)
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(field,))
            for field in CONCURRENT_FIELDS
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert not errors, errors
        assert all(r.ok for r in results.values())
        flags = read_store(store)["flags"]
        assert set(flags) == set(CONCURRENT_FIELDS), (
            "a concurrent writer's key was lost to a stale read-modify-write"
        )

    def test_concurrent_threads_produce_one_audit_line_each(
        self, store: Path, live: Settings, no_dotenv
    ):
        """O_APPEND makes concurrent short-line appends interleave by LINE, so
        every record must still parse."""
        barrier = threading.Barrier(len(CONCURRENT_FIELDS))

        def worker(field: str) -> None:
            barrier.wait(timeout=30)
            writer.write_override(field, 7, path=store)

        threads = [
            threading.Thread(target=worker, args=(field,))
            for field in CONCURRENT_FIELDS
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        records = audit_records(store)  # parses every line; raises on a mangled one
        assert len(records) == len(CONCURRENT_FIELDS)
        assert {r["key"] for r in records} == set(CONCURRENT_FIELDS)

    def test_an_externally_added_key_survives_the_next_write(
        self, store: Path, live: Settings, no_dotenv
    ):
        """Stands in for a cross-process writer that landed before this call:
        the file is read INSIDE the call, not cached from an earlier point."""
        writer.write_override(FIELD, 300, path=store)
        payload = read_store(store)
        payload["flags"]["MACRO_REFRESH_HOURS"] = {
            "value": 6,
            "updated_at": "2020-01-01T00:00:00+00:00",
            "updated_by": "another-process",
        }
        store.write_text(json.dumps(payload), encoding="utf-8")

        writer.write_override(OTHER_FIELD, 0.4, path=store)

        flags = read_store(store)["flags"]
        assert flags["MACRO_REFRESH_HOURS"]["updated_by"] == "another-process"
        assert set(flags) == {FIELD, OTHER_FIELD, "MACRO_REFRESH_HOURS"}


# ===========================================================================
# The write is atomic — temp file + os.replace, never in place
# ===========================================================================


class TestAtomicWrite:
    """The store must never be written in place.

    A direct ``store.write_text(...)`` would pass every other test in this
    file — verified by mutation: swapping ``_atomic_write_json``'s temp-file
    body for an in-place write left all 100 other tests green. The claim is
    load-bearing (a crash or a full disk partway through an in-place write
    truncates the operator's whole override set, and ``runtime_flags.py`` reads
    this file at every ``import settings``), so it is pinned directly.
    """

    def test_the_store_is_replaced_via_a_temp_file_never_written_in_place(
        self, store: Path, live: Settings, no_dotenv, monkeypatch: pytest.MonkeyPatch
    ):
        """The anti-in-place pin: ``os.replace`` must be what publishes the new
        contents, and its source must be a temp sibling — not the store."""
        calls: list[tuple[str, str]] = []
        real_replace = os.replace

        def spy(src, dst, *args, **kwargs):
            calls.append((str(src), str(dst)))
            return real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(writer.os, "replace", spy)
        result = writer.write_override(FIELD, 300, path=store)

        assert result.ok is True
        assert len(calls) == 1, "the store must be published by exactly one replace"
        src, dst = calls[0]
        assert dst == str(store.resolve())
        assert Path(src).name.startswith(f"{store.name}.tmp."), (
            "the new contents must be staged in a temp sibling, not written "
            "into the store directly"
        )
        assert Path(src).parent == store.resolve().parent, (
            "the temp file must share the store's directory, or os.replace is "
            "a cross-filesystem copy and no longer atomic"
        )

    def test_a_failure_at_the_publish_step_leaves_the_previous_store_intact(
        self, store: Path, live: Settings, no_dotenv
    ):
        """The property atomicity actually buys: a failed write is a no-op, not
        a truncated file. An in-place writer would have already clobbered the
        previous overrides before it could fail."""
        writer.write_override(FIELD, 300, actor="first", path=store)
        writer.write_override(OTHER_FIELD, 0.4, actor="first", path=store)
        before = store.read_text(encoding="utf-8")

        def boom(src, dst, *args, **kwargs):
            raise OSError(28, "No space left on device")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(writer.os, "replace", boom)
            result = writer.write_override(FIELD, 999, path=store)

        assert result.ok is False
        assert result.persisted is False
        assert result.applies == writer.APPLIES_REFUSED
        # The whole previous store survives, byte for byte.
        assert store.read_text(encoding="utf-8") == before
        flags, error = runtime_flags.load_store(store)
        assert error is None, "the store must still be loadable by the read path"
        assert flags == {FIELD: 300, OTHER_FIELD: 0.4}
        # And the failed value never went live either.
        assert live.BETA_LOOKBACK_DAYS == 300

    def test_no_temp_file_is_left_behind_when_the_write_fails(
        self, store: Path, live: Settings, no_dotenv
    ):
        """``output/`` is an operator-visible directory; a failed write must not
        litter it with orphaned ``.tmp.<pid>.<tid>`` files."""

        def boom(src, dst, *args, **kwargs):
            raise OSError(28, "No space left on device")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(writer.os, "replace", boom)
            writer.write_override(FIELD, 300, path=store)

        assert not list(store.parent.glob(f"{store.name}.tmp*")), (
            "a failed write left its temp file behind"
        )

    def test_a_successful_write_leaves_no_temp_file(
        self, store: Path, live: Settings, no_dotenv
    ):
        writer.write_override(FIELD, 300, path=store)
        assert not list(store.parent.glob(f"{store.name}.tmp*"))


# ===========================================================================
# A damaged store — the two modes are handled differently on purpose
# ===========================================================================


class TestDamagedStore:
    def test_unparseable_store_is_quarantined_not_destroyed(
        self, store: Path, live: Settings, no_dotenv, caplog
    ):
        corrupt = '{"version": 1, "flags": {"BETA_LOOK'
        store.write_text(corrupt, encoding="utf-8")

        with caplog.at_level("WARNING"):
            result = writer.write_override(FIELD, 300, path=store)

        assert result.ok is True
        assert read_store(store)["flags"][FIELD]["value"] == 300

        quarantined = sorted(store.parent.glob(f"{store.name}.corrupt.*"))
        assert len(quarantined) == 1, "the damaged bytes must be preserved"
        assert quarantined[0].read_text(encoding="utf-8") == corrupt
        assert "damaged" in caplog.text

    def test_a_newer_schema_version_is_REFUSED_not_overwritten(
        self, store: Path, live: Settings, no_dotenv
    ):
        """Not corruption — a valid file from a newer build. Overwriting it
        with a version-1 file would destroy current state this build simply
        cannot read."""
        payload = {
            "version": runtime_flags.SCHEMA_VERSION + 1,
            "flags": {"SOMETHING_NEW": {"value": 1}},
        }
        before = json.dumps(payload)
        store.write_text(before, encoding="utf-8")

        result = writer.write_override(FIELD, 300, path=store)

        assert result.ok is False
        assert result.persisted is False
        assert result.applies == writer.APPLIES_REFUSED
        assert "newer build" in result.reason
        assert store.read_text(encoding="utf-8") == before
        assert not list(store.parent.glob(f"{store.name}.corrupt.*"))

    def test_a_top_level_array_is_quarantined(
        self, store: Path, live: Settings, no_dotenv
    ):
        """No ``version`` to read, so it cannot be a newer build — treat as
        damaged."""
        store.write_text("[1, 2, 3]", encoding="utf-8")

        result = writer.write_override(FIELD, 300, path=store)

        assert result.ok is True
        assert len(list(store.parent.glob(f"{store.name}.corrupt.*"))) == 1

    def test_delete_against_a_newer_schema_version_is_also_refused(
        self, store: Path, live: Settings, no_dotenv
    ):
        payload = {"version": runtime_flags.SCHEMA_VERSION + 1, "flags": {}}
        before = json.dumps(payload)
        store.write_text(before, encoding="utf-8")

        result = writer.delete_override(FIELD, path=store)

        assert result.ok is False
        assert "newer build" in result.reason
        assert store.read_text(encoding="utf-8") == before


# ===========================================================================
# CONSTRAINT #6 — never raise
# ===========================================================================


class TestNeverRaises:
    def test_an_unwritable_store_location_degrades(
        self, tmp_path: Path, live: Settings, no_dotenv
    ):
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file, not a directory", encoding="utf-8")
        store = blocker / "runtime_flags.json"

        result = writer.write_override(FIELD, 300, path=store)

        assert result.ok is False
        assert result.persisted is False
        assert result.applies == writer.APPLIES_REFUSED
        assert live.BETA_LOOKBACK_DAYS != 300

    def test_an_audit_log_failure_does_not_flip_a_successful_write(
        self, store: Path, live: Settings, no_dotenv, caplog
    ):
        """The value IS persisted and live at that point, so reporting failure
        would invite a retry that double-writes. The gap is logged at ERROR
        instead."""
        writer.audit_path(store).mkdir(parents=True)  # open(..., "a") now fails

        with caplog.at_level("ERROR"):
            result = writer.write_override(FIELD, 300, path=store)

        assert result.ok is True
        assert result.persisted is True
        assert result.applies == writer.APPLIES_IMMEDIATELY
        assert live.BETA_LOOKBACK_DAYS == 300
        assert "audit record" in caplog.text

    def test_a_non_string_key_is_refused_rather_than_exploding(
        self, store: Path, live: Settings
    ):
        result = writer.write_override(None, 300, path=store)  # type: ignore[arg-type]
        assert result.ok is False
        assert result.applies == writer.APPLIES_REFUSED

    def test_a_broken_settings_singleton_degrades(
        self, store: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The outermost net: anything unanticipated still returns a
        ``WriteResult``."""

        class Exploding:
            def __getattr__(self, name):
                raise RuntimeError("boom")

        monkeypatch.setattr(settings_module, "settings", Exploding())
        result = writer.write_override(FIELD, 300, path=store)

        assert result.ok is False
        assert result.applies == writer.APPLIES_REFUSED
        assert "unexpected writer failure" in result.reason


# ===========================================================================
# The real singleton, in a fresh interpreter
# ===========================================================================


class TestFreshInterpreter:
    """Proves the "applies immediately in the process that served the write"
    claim against a REAL ``import settings``, not a throwaway ``Settings()``.

    This pytest process imported ``settings`` at collection time and ~1300
    other tests are free to have patched the singleton since, so an in-process
    assertion here would be measuring test pollution rather than the property.
    """

    WRITE = """
import json
import runtime_flags_writer as w
import settings as sm

before = sm.settings.BETA_LOOKBACK_DAYS
result = w.write_override("BETA_LOOKBACK_DAYS", 4321, actor="subproc")
print(json.dumps({
    "before": before,
    "ok": result.ok,
    "applies": result.applies,
    "persisted": result.persisted,
    "applied_value": result.applied_value,
    "live_after": sm.settings.BETA_LOOKBACK_DAYS,
}))
"""

    READ_BACK = """
import settings as sm
print(sm.settings.BETA_LOOKBACK_DAYS)
"""

    DELETE = """
import json
import runtime_flags_writer as w
import settings as sm

result = w.delete_override("BETA_LOOKBACK_DAYS", actor="subproc")
print(json.dumps({
    "ok": result.ok,
    "applies": result.applies,
    "persisted": result.persisted,
    "live_after": sm.settings.BETA_LOOKBACK_DAYS,
}))
"""

    def test_a_write_is_live_in_the_process_that_served_it(self, tmp_path: Path):
        store = tmp_path / "runtime_flags.json"
        out = json.loads(
            run_in_fresh_interpreter(self.WRITE, store, scrub_env=(FIELD,))
        )

        assert out["ok"] is True
        assert out["applies"] == "immediately"
        assert out["persisted"] is True
        assert out["before"] != 4321
        assert out["applied_value"] == 4321
        assert out["live_after"] == 4321

    def test_the_write_survives_into_a_brand_new_process(self, tmp_path: Path):
        """The durability half: a SECOND fresh interpreter that only does
        ``import settings`` picks the override up through the read path."""
        store = tmp_path / "runtime_flags.json"
        run_in_fresh_interpreter(self.WRITE, store, scrub_env=(FIELD,))

        out = run_in_fresh_interpreter(self.READ_BACK, store, scrub_env=(FIELD,))
        assert out == "4321"

    def test_a_delete_reverts_a_brand_new_process_too(self, tmp_path: Path):
        store = tmp_path / "runtime_flags.json"
        run_in_fresh_interpreter(self.WRITE, store, scrub_env=(FIELD,))

        out = json.loads(
            run_in_fresh_interpreter(self.DELETE, store, scrub_env=(FIELD,))
        )
        assert out["ok"] is True
        assert out["persisted"] is True
        assert out["live_after"] != 4321

        after = run_in_fresh_interpreter(self.READ_BACK, store, scrub_env=(FIELD,))
        assert after != "4321"

    def test_the_real_default_store_path_is_under_output(self, tmp_path: Path):
        """Nothing in this test file writes there, so assert the wiring
        separately rather than by touching it.

        The store is anchored under settings.LOCAL_DATA_ROOT's default
        literal (Path.home() / ".stockpy_local"), not the repo root — see
        runtime_flags.py's DEFAULT_STORE_PATH docstring for why it hardcodes
        this independently rather than importing settings.LOCAL_DATA_ROOT."""
        assert (
            runtime_flags.DEFAULT_STORE_PATH.parent
            == Path.home() / ".stockpy_local" / "output"
        )
        assert writer.AUDIT_FILENAME == "runtime_flags_audit.jsonl"
