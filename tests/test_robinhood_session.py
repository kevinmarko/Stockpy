"""
tests/test_robinhood_session.py
================================
Tests for data/robinhood_session.py against a tmp_path-based fake ~/.tokens
directory -- the module's _TOKENS_DIR / _PICKLE_PATH / _BACKUP_PATH
module-level constants are monkeypatched to point into tmp_path, so no real
filesystem state under the real home directory is ever read or written.

No Robinhood network calls happen anywhere in this file -- the module itself
is read-only with respect to Robinhood (see its own module docstring).
"""

from __future__ import annotations

import pickle
import stat
import sys
from pathlib import Path

import pytest

import data.robinhood_session as robinhood_session


def _write_valid_session(path: Path, **overrides) -> None:
    """Write a pickle carrying the required keys _is_loadable_session checks
    for (access_token/device_token/token_type) -- never real token values."""
    payload = {"access_token": "tok", "device_token": "dev", "token_type": "Bearer"}
    payload.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        pickle.dump(payload, fh)


@pytest.fixture
def _fake_tokens_dir(tmp_path, monkeypatch):
    tokens_dir = tmp_path / ".tokens"
    pickle_path = tokens_dir / "robinhood.pickle"
    backup_path = pickle_path.with_suffix(".pickle.bak")
    monkeypatch.setattr(robinhood_session, "_TOKENS_DIR", tokens_dir)
    monkeypatch.setattr(robinhood_session, "_PICKLE_PATH", pickle_path)
    monkeypatch.setattr(robinhood_session, "_BACKUP_PATH", backup_path)
    return tokens_dir, pickle_path, backup_path


# ---------------------------------------------------------------------------
# _is_loadable_session
# ---------------------------------------------------------------------------

class TestIsLoadableSession:
    def test_missing_file_is_not_loadable(self, _fake_tokens_dir) -> None:
        _, pickle_path, _ = _fake_tokens_dir
        assert robinhood_session._is_loadable_session(pickle_path) is False

    def test_empty_file_is_not_loadable(self, _fake_tokens_dir) -> None:
        _, pickle_path, _ = _fake_tokens_dir
        pickle_path.parent.mkdir(parents=True, exist_ok=True)
        pickle_path.write_bytes(b"")
        assert robinhood_session._is_loadable_session(pickle_path) is False

    def test_garbage_bytes_are_not_loadable(self, _fake_tokens_dir) -> None:
        _, pickle_path, _ = _fake_tokens_dir
        pickle_path.parent.mkdir(parents=True, exist_ok=True)
        pickle_path.write_bytes(b"not a pickle, just garbage bytes")
        assert robinhood_session._is_loadable_session(pickle_path) is False

    def test_valid_pickle_missing_required_keys_is_not_loadable(self, _fake_tokens_dir) -> None:
        """A dict that unpickles fine but lacks the required session keys
        (access_token/device_token/token_type) must not be trusted."""
        _, pickle_path, _ = _fake_tokens_dir
        pickle_path.parent.mkdir(parents=True, exist_ok=True)
        with pickle_path.open("wb") as fh:
            pickle.dump({"access_token": "tok"}, fh)
        assert robinhood_session._is_loadable_session(pickle_path) is False

    def test_non_dict_pickle_is_not_loadable(self, _fake_tokens_dir) -> None:
        _, pickle_path, _ = _fake_tokens_dir
        pickle_path.parent.mkdir(parents=True, exist_ok=True)
        with pickle_path.open("wb") as fh:
            pickle.dump(["not", "a", "dict"], fh)
        assert robinhood_session._is_loadable_session(pickle_path) is False

    def test_valid_session_is_loadable(self, _fake_tokens_dir) -> None:
        _, pickle_path, _ = _fake_tokens_dir
        _write_valid_session(pickle_path)
        assert robinhood_session._is_loadable_session(pickle_path) is True


# ---------------------------------------------------------------------------
# ensure_session_pickle -- called BEFORE a login attempt
# ---------------------------------------------------------------------------

class TestEnsureSessionPickle:
    def test_restores_from_valid_backup_when_primary_is_zero_bytes(self, _fake_tokens_dir) -> None:
        """A 0-byte primary is the signature of a kill-mid-write artifact
        (robin_stocks' own pickle write is non-atomic) -- a valid backup
        must be restored so the login presents the SAME device_token
        Robinhood already approved."""
        _tokens_dir, pickle_path, backup_path = _fake_tokens_dir
        _write_valid_session(backup_path)
        pickle_path.parent.mkdir(parents=True, exist_ok=True)
        pickle_path.write_bytes(b"")

        robinhood_session.ensure_session_pickle()

        assert robinhood_session._is_loadable_session(pickle_path)
        with pickle_path.open("rb") as fh:
            restored = pickle.load(fh)
        assert restored["access_token"] == "tok"
        assert restored["device_token"] == "dev"

    def test_refuses_corrupt_backup_and_removes_corrupt_primary(self, _fake_tokens_dir) -> None:
        """When NEITHER the primary nor the backup is a valid session, the
        corrupt primary is removed (so robin_stocks doesn't trip over an
        unloadable file) rather than restored from equally-corrupt bytes."""
        _tokens_dir, pickle_path, backup_path = _fake_tokens_dir
        pickle_path.parent.mkdir(parents=True, exist_ok=True)
        pickle_path.write_bytes(b"corrupt-primary-bytes")
        backup_path.write_bytes(b"corrupt-backup-garbage, not a real pickle")

        robinhood_session.ensure_session_pickle()

        assert not pickle_path.exists()
        # The (also-corrupt) backup is untouched -- this function only ever
        # READS the backup, never writes or removes it.
        assert backup_path.exists()

    def test_noop_when_primary_already_valid(self, _fake_tokens_dir) -> None:
        """A valid primary must never be overwritten from the backup, even
        when a DIFFERENT valid backup exists -- doing so would silently
        switch device sessions underneath an already-working login."""
        _tokens_dir, pickle_path, backup_path = _fake_tokens_dir
        _write_valid_session(pickle_path)
        original_bytes = pickle_path.read_bytes()
        _write_valid_session(backup_path, access_token="different-tok", device_token="different-dev")

        robinhood_session.ensure_session_pickle()

        assert pickle_path.read_bytes() == original_bytes

    def test_noop_when_neither_primary_nor_backup_exist(self, _fake_tokens_dir) -> None:
        _tokens_dir, pickle_path, backup_path = _fake_tokens_dir
        robinhood_session.ensure_session_pickle()  # must not raise
        assert not pickle_path.exists()
        assert not backup_path.exists()

    def test_never_raises_on_unexpected_error(self, _fake_tokens_dir, monkeypatch) -> None:
        """Best-effort by design (see module docstring) -- any unexpected
        failure is swallowed, never propagated to the login caller."""
        _tokens_dir, pickle_path, _backup_path = _fake_tokens_dir

        def boom(*args, **kwargs):
            raise RuntimeError("simulated filesystem error")

        monkeypatch.setattr(robinhood_session, "_is_loadable_session", boom)

        robinhood_session.ensure_session_pickle()  # must not raise


# ---------------------------------------------------------------------------
# backup_session_pickle -- called AFTER a successful login
# ---------------------------------------------------------------------------

class TestBackupSessionPickle:
    def test_noop_when_primary_missing(self, _fake_tokens_dir) -> None:
        _tokens_dir, _pickle_path, backup_path = _fake_tokens_dir
        robinhood_session.backup_session_pickle()
        assert not backup_path.exists()

    def test_noop_when_primary_corrupt(self, _fake_tokens_dir) -> None:
        _tokens_dir, pickle_path, backup_path = _fake_tokens_dir
        pickle_path.parent.mkdir(parents=True, exist_ok=True)
        pickle_path.write_bytes(b"corrupt, not a real pickle")

        robinhood_session.backup_session_pickle()

        assert not backup_path.exists()

    def test_successful_backup_is_byte_identical_to_primary(self, _fake_tokens_dir) -> None:
        _tokens_dir, pickle_path, backup_path = _fake_tokens_dir
        _write_valid_session(pickle_path)
        original_bytes = pickle_path.read_bytes()

        robinhood_session.backup_session_pickle()

        assert backup_path.exists()
        assert backup_path.read_bytes() == original_bytes

    def test_successful_backup_tightens_permissions_to_0600(self, _fake_tokens_dir) -> None:
        """Permission-tightening is exercised on POSIX platforms only --
        os.chmod's actual enforcement of these bits is filesystem-dependent
        (e.g. unreliable on some CI mounts / non-POSIX filesystems), so this
        assertion is skipped on Windows rather than asserting something this
        test can't reliably guarantee there."""
        if sys.platform == "win32":
            pytest.skip("POSIX permission bits are not meaningfully testable on Windows")

        _tokens_dir, pickle_path, backup_path = _fake_tokens_dir
        _write_valid_session(pickle_path)

        robinhood_session.backup_session_pickle()

        assert stat.S_IMODE(pickle_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600

    def test_backup_write_is_atomic_tmp_sibling_removed(self, _fake_tokens_dir) -> None:
        _tokens_dir, pickle_path, backup_path = _fake_tokens_dir
        _write_valid_session(pickle_path)

        robinhood_session.backup_session_pickle()

        tmp_sibling = backup_path.with_suffix(".tmp")
        assert not tmp_sibling.exists(), ".tmp file left behind after a successful backup"

    def test_never_raises_on_unexpected_error(self, _fake_tokens_dir, monkeypatch) -> None:
        _tokens_dir, pickle_path, _backup_path = _fake_tokens_dir
        _write_valid_session(pickle_path)

        def boom(*args, **kwargs):
            raise RuntimeError("simulated filesystem error")

        monkeypatch.setattr(robinhood_session.shutil, "copy2", boom)

        robinhood_session.backup_session_pickle()  # must not raise
