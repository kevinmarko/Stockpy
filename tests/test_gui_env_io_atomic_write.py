"""
tests/test_gui_env_io_atomic_write.py
=====================================
Tests for ``gui.env_io.write_many_atomic`` — the all-or-nothing multi-key ``.env``
writer used by ``PUT /strategy/modules`` to write SIGNAL_WEIGHTS +
DISABLED_SIGNAL_MODULES as ONE logical unit.

The whole point is the failure mode ``write_many`` does NOT protect against: a
mid-write crash leaving a half-applied config (new weights + a stale disabled-set
silently changes what the platform recommends). These tests pin that a failure
leaves the ``.env`` byte-identical with no ``.tmp`` residue, that validation
happens before any write, and that ``write_many`` itself stays non-atomic.

All writes are redirected to a temp ``.env`` via monkeypatching ``env_io.ENV_PATH``.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from dotenv import dotenv_values

from gui import env_io


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# InvestYo config (test fixture)\n"
        "FRED_API_KEY=super-secret-value\n"
        "RISK_FREE_RATE=0.045\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_io, "ENV_PATH", env_file)
    return env_file


def test_write_many_atomic_happy_path_writes_both_keys(temp_env):
    written = env_io.write_many_atomic(
        {"SIGNAL_WEIGHTS": {"a": 1.0, "b": 2.0}, "DISABLED_SIGNAL_MODULES": ["b"]}
    )
    assert written == ["SIGNAL_WEIGHTS", "DISABLED_SIGNAL_MODULES"]
    vals = dotenv_values(temp_env)
    assert json.loads(vals["SIGNAL_WEIGHTS"]) == {"a": 1.0, "b": 2.0}
    assert json.loads(vals["DISABLED_SIGNAL_MODULES"]) == ["b"]


def test_leaves_env_byte_identical_on_mid_write_failure(temp_env):
    before = temp_env.read_bytes()
    real_set_key = env_io.set_key
    calls = {"n": 0}

    def flaky_set_key(path, key, value, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # fail on the SECOND key
            raise RuntimeError("simulated dotenv failure")
        return real_set_key(path, key, value, **kwargs)

    with mock.patch.object(env_io, "set_key", flaky_set_key):
        with pytest.raises(RuntimeError):
            env_io.write_many_atomic(
                {"SIGNAL_WEIGHTS": {"a": 1.0}, "DISABLED_SIGNAL_MODULES": ["b"]}
            )
    # Original file untouched...
    assert temp_env.read_bytes() == before
    # ...and no .tmp residue left behind.
    assert not (temp_env.parent / (temp_env.name + ".tmp")).exists()


def test_validates_all_keys_before_any_write(temp_env):
    before = temp_env.read_bytes()
    # One good key, one disallowed key: nothing should be written.
    with pytest.raises(env_io.DisallowedKeyError):
        env_io.write_many_atomic(
            {"SIGNAL_WEIGHTS": {"a": 1.0}, "NOT_A_REAL_KEY": "x"}
        )
    assert temp_env.read_bytes() == before


def test_rejects_secret_key_without_writing(temp_env):
    before = temp_env.read_bytes()
    with pytest.raises(env_io.SecretWriteError):
        env_io.write_many_atomic({"SIGNAL_WEIGHTS": {"a": 1.0}, "FRED_API_KEY": "nope"})
    assert temp_env.read_bytes() == before


def test_json_keys_round_trip(temp_env):
    env_io.write_many_atomic(
        {"SIGNAL_WEIGHTS": {"macd_momentum": 12.5}, "DISABLED_SIGNAL_MODULES": []}
    )
    vals = dotenv_values(temp_env)
    assert json.loads(vals["SIGNAL_WEIGHTS"]) == {"macd_momentum": 12.5}
    assert json.loads(vals["DISABLED_SIGNAL_MODULES"]) == []


def test_preserves_unrelated_lines(temp_env):
    env_io.write_many_atomic({"SIGNAL_WEIGHTS": {"a": 1.0}})
    text = temp_env.read_text(encoding="utf-8")
    assert "# InvestYo config (test fixture)" in text
    assert "FRED_API_KEY=super-secret-value" in text
    assert "RISK_FREE_RATE=0.045" in text


def test_preserves_file_mode(temp_env):
    temp_env.chmod(0o600)
    env_io.write_many_atomic({"SIGNAL_WEIGHTS": {"a": 1.0}})
    assert (temp_env.stat().st_mode & 0o777) == 0o600


def test_write_many_stays_non_atomic(temp_env):
    """Regression guard: the EXISTING write_many is intentionally left
    partial-write-prone (it applies set_key one at a time). If a future refactor
    made it atomic, this test would flip — and callers relying on write_many's
    documented behavior should be re-examined."""
    real_set_key = env_io.set_key
    calls = {"n": 0}

    def flaky_set_key(path, key, value, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real_set_key(path, key, value, **kwargs)

    with mock.patch.object(env_io, "set_key", flaky_set_key):
        with pytest.raises(RuntimeError):
            env_io.write_many({"RISK_FREE_RATE": 0.05, "KELLY_FRACTION": 0.5})
    # The FIRST key landed despite the second failing — the non-atomic behavior.
    vals = dotenv_values(temp_env)
    assert vals.get("RISK_FREE_RATE") == "0.05"
    assert "KELLY_FRACTION" not in vals
