"""Guards robin_stocks' `~/.tokens/robinhood.pickle` session file against the
device-token churn that forces a Robinhood device-approval challenge on every
login. Read-only with respect to Robinhood itself -- no network calls, only
local filesystem operations on the pickle robin_stocks already owns.

Root cause: robin_stocks generates a fresh random `device_token` on every
`login()` call UNLESS a loadable session pickle overrides it with a
previously-stored one. Presenting a brand-new device to Robinhood on every
login is exactly what keeps triggering re-verification. The library's own
write of that pickle is non-atomic (truncate-then-dump), so a process killed
mid-write -- which a deadline-enforced login worker will eventually do --
leaves a 0-byte, unloadable file behind. `ensure_session_pickle()` restores a
known-good backup before a login attempt; `backup_session_pickle()` saves a
fresh one after a successful login, atomically.
"""

from __future__ import annotations

import logging
import os
import pickle
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_TOKENS_DIR = Path.home() / ".tokens"
_PICKLE_PATH = _TOKENS_DIR / "robinhood.pickle"
_BACKUP_PATH = _PICKLE_PATH.with_suffix(".pickle.bak")

# Keys robin_stocks' own login response stores in the session pickle
# (authentication.py's `update_session_data`). Used only to sanity-check that
# a file we're about to trust/restore is a real session, not garbage --
# never inspected for their VALUES (no token material is logged or returned).
_REQUIRED_KEYS = frozenset({"access_token", "device_token", "token_type"})


def _is_loadable_session(path: Path) -> bool:
    """True if `path` exists, is non-empty, and unpickles to a dict carrying
    the fields a real robin_stocks session pickle has. Never raises -- any
    failure (missing file, empty file, corrupt pickle, wrong shape) is just
    `False`."""
    try:
        if not path.exists() or path.stat().st_size == 0:
            return False
        with path.open("rb") as fh:
            data = pickle.load(fh)
        return isinstance(data, dict) and _REQUIRED_KEYS <= data.keys()
    except Exception:
        return False


def ensure_session_pickle() -> None:
    """Called BEFORE attempting a login. If the primary pickle is missing,
    empty, or corrupt, restores it from `.pickle.bak` when that backup is
    itself a valid session -- so this login presents the SAME device_token
    Robinhood has already approved, rather than a fresh random one.

    If there's no valid backup either, removes a corrupt/empty primary file
    (if present) so robin_stocks doesn't hit its own noisy "could not load
    pickle" fallback path; a missing file and a freshly-deleted one behave
    identically to the library. Never raises -- this is a best-effort
    optimization, not a login precondition.
    """
    try:
        if _is_loadable_session(_PICKLE_PATH):
            return
        if _is_loadable_session(_BACKUP_PATH):
            _TOKENS_DIR.mkdir(mode=0o700, exist_ok=True)
            tmp_path = _PICKLE_PATH.with_suffix(".tmp")
            shutil.copy2(_BACKUP_PATH, tmp_path)
            os.replace(tmp_path, _PICKLE_PATH)
            logger.info("Restored Robinhood session pickle from backup.")
            return
        if _PICKLE_PATH.exists():
            # Corrupt/empty and no usable backup -- clear it rather than let
            # robin_stocks trip over an unloadable file mid-login.
            _PICKLE_PATH.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - best-effort, never blocks login
        logger.debug("ensure_session_pickle: non-fatal failure: %s", exc)


def backup_session_pickle() -> None:
    """Called AFTER a successful login. Copies the now-fresh primary pickle
    to `.pickle.bak` atomically (write to a `.tmp` sibling, then `os.replace`)
    so a future kill mid-write to the PRIMARY never corrupts the backup too.
    Tightens permissions on both files and the containing directory, since
    the pickle carries a live OAuth access token that robin_stocks itself
    never restricts. Never raises.
    """
    try:
        if not _is_loadable_session(_PICKLE_PATH):
            return
        tmp_path = _BACKUP_PATH.with_suffix(".tmp")
        shutil.copy2(_PICKLE_PATH, tmp_path)
        os.replace(tmp_path, _BACKUP_PATH)
        os.chmod(_BACKUP_PATH, 0o600)
        os.chmod(_PICKLE_PATH, 0o600)
        _TOKENS_DIR.chmod(0o700)
    except Exception as exc:  # noqa: BLE001 - best-effort, never blocks login
        logger.debug("backup_session_pickle: non-fatal failure: %s", exc)
