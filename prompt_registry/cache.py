"""
prompt_registry/cache.py
=========================
Disk cache and baseline fallback for the Prompt Registry.

Two distinct stores are defined here:

``CacheManager``
    Persistent, process-independent disk cache under ``output/prompt_cache/``.
    Stores full :class:`~prompt_registry.models.PromptRecord` instances (JSON
    with body + sha256 + signature + metadata) so the registry can verify
    integrity on cache reads (Stage 3).

    * One subdirectory per prompt ID (dots → underscores to avoid nested dirs)
    * One JSON file per version inside that subdirectory
    * ``write()`` is atomic: write-then-rename to a ``.tmp`` file
    * ``_prune()`` removes the oldest files beyond ``keep_versions`` after
      every successful write
    * All read/write failures are caught and degrade gracefully — never raise
      to the caller (CONSTRAINT #6)

``read_baseline(prompt_id)``
    Reads the corresponding ``.md`` file from the ``baseline/`` directory that
    ships with the package.  Returns the raw body text (str) or ``None`` for
    an unknown id.  This is always the last-resort fallback in the resolution
    chain (Stage 3); it is never empty for any known prompt id.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

from prompt_registry.models import PromptRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Baseline directory (shipped with the package)
# ---------------------------------------------------------------------------

_BASELINE_DIR: Path = Path(__file__).parent / "baseline"
"""Directory containing the committed fail-closed prompt defaults."""

# Canonical mapping: prompt_id → baseline filename (without .md extension).
# Both ``gravity.system`` and ``gravity.step_01`` use dots as separators;
# the corresponding files use underscores so they're safe on all filesystems.
_BASELINE_FILEMAP: Dict[str, str] = {
    "master_preprompt": "master_preprompt",
    "gravity.system": "gravity_system",
    "gravity.step_01": "gravity_step_01",
    "gravity.step_02": "gravity_step_02",
    "gravity.step_03": "gravity_step_03",
    "gravity.step_04": "gravity_step_04",
    "gravity.step_05": "gravity_step_05",
    "gravity.step_06": "gravity_step_06",
    "gravity.step_07": "gravity_step_07",
}


def read_baseline(prompt_id: str) -> Optional[str]:
    """Return the committed baseline body text for *prompt_id*.

    Uses the in-package ``baseline/`` directory as a fail-closed fallback.
    Returns ``None`` for any unrecognised prompt id; never raises.

    Parameters
    ----------
    prompt_id:
        Registry ID such as ``"gravity.step_01"`` or ``"master_preprompt"``.

    Returns
    -------
    str or None
        The raw body text, or ``None`` when *prompt_id* is not in the
        baseline set or the file cannot be read.
    """
    stem = _BASELINE_FILEMAP.get(prompt_id)
    if stem is None:
        logger.debug("read_baseline: no baseline file for prompt_id=%r", prompt_id)
        return None
    path = _BASELINE_DIR / f"{stem}.md"
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning(
            "read_baseline: failed to read %s for prompt_id=%r: %s",
            path, prompt_id, exc,
        )
        return None


def list_baseline_ids() -> List[str]:
    """Return all prompt IDs that have a committed baseline file."""
    return list(_BASELINE_FILEMAP.keys())


# ---------------------------------------------------------------------------
# Default settings (overridden by settings.py in Stage 6)
# ---------------------------------------------------------------------------

_DEFAULT_KEEP_VERSIONS: int = 5
"""How many signed versions to retain per prompt ID after each write."""

_DEFAULT_CACHE_DIR: Path = Path("output") / "prompt_cache"
"""Default root directory for the disk cache."""


# ---------------------------------------------------------------------------
# CacheManager
# ---------------------------------------------------------------------------

def _sanitize_id(prompt_id: str) -> str:
    """Convert a prompt id to a safe directory name.

    Dots and slashes are replaced with underscores so that ``gravity.step_01``
    becomes ``gravity_step_01`` on the filesystem.

    Examples
    --------
    >>> _sanitize_id("gravity.step_01")
    'gravity_step_01'
    >>> _sanitize_id("master_preprompt")
    'master_preprompt'
    """
    return prompt_id.replace(".", "_").replace("/", "_").replace(" ", "_")


class CacheManager:
    """Signed-version disk cache for the Prompt Registry.

    Layout on disk::

        output/prompt_cache/
          gravity_step_01/
            1.0.0.json
            1.0.1.json
          master_preprompt/
            1.3.0.json

    Each ``.json`` file is the serialised :class:`~prompt_registry.models.PromptRecord`
    (body, sha256, signature, created_at, author, notes).

    Parameters
    ----------
    cache_dir:
        Root directory for the cache (default ``output/prompt_cache/``).
    keep_versions:
        Maximum number of versions to retain per prompt ID.  Oldest files
        (by mtime) are pruned after each successful write.  Minimum 1.
    """

    def __init__(
        self,
        cache_dir: Union[str, Path] = _DEFAULT_CACHE_DIR,
        *,
        keep_versions: int = _DEFAULT_KEEP_VERSIONS,
    ) -> None:
        self._dir = Path(cache_dir)
        self._keep = max(1, keep_versions)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _prompt_dir(self, prompt_id: str) -> Path:
        return self._dir / _sanitize_id(prompt_id)

    def _record_path(self, prompt_id: str, version: str) -> Path:
        safe_version = version.replace("/", "_").replace(" ", "_")
        return self._prompt_dir(prompt_id) / f"{safe_version}.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_versions(self, prompt_id: str) -> List[str]:
        """List cached versions for *prompt_id*, newest-first by mtime.

        Returns an empty list when no versions are cached or on any error.
        """
        try:
            d = self._prompt_dir(prompt_id)
            if not d.is_dir():
                return []
            files = sorted(
                d.glob("*.json"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            return [f.stem for f in files]
        except Exception as exc:
            logger.debug(
                "CacheManager.list_versions: error listing %r: %s", prompt_id, exc
            )
            return []

    def read(self, prompt_id: str, version: str) -> Optional[PromptRecord]:
        """Read a cached :class:`~prompt_registry.models.PromptRecord`.

        Returns ``None`` on a cache miss or any read/parse error.  Never raises.
        """
        try:
            path = self._record_path(prompt_id, version)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return PromptRecord.from_dict(data)
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.debug(
                "CacheManager.read: error reading %r@%s: %s", prompt_id, version, exc
            )
            return None

    def read_latest(self, prompt_id: str) -> Optional[PromptRecord]:
        """Return the most recently written cached record for *prompt_id*.

        Returns ``None`` when no versions are cached or on any error.
        """
        versions = self.list_versions(prompt_id)
        if not versions:
            return None
        return self.read(prompt_id, versions[0])

    def write(self, prompt_id: str, version: str, record: PromptRecord) -> bool:
        """Write *record* to the cache.

        Uses atomic write-then-rename so a crash during the write never
        leaves a partial file.

        Parameters
        ----------
        prompt_id:
            Registry prompt id (e.g. ``"gravity.step_01"``).
        version:
            Version string (e.g. ``"1.0.0"``).
        record:
            Signed :class:`~prompt_registry.models.PromptRecord` to persist.

        Returns
        -------
        bool
            ``True`` on success; ``False`` on any failure (never raises).
        """
        try:
            dest = self._record_path(prompt_id, version)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(record.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.rename(dest)
            logger.debug("CacheManager.write: wrote %r@%s → %s", prompt_id, version, dest)
            self._prune(prompt_id)
            return True
        except Exception as exc:
            logger.warning(
                "CacheManager.write: failed for %r@%s: %s", prompt_id, version, exc
            )
            # Clean up any orphaned .tmp file
            try:
                tmp = self._record_path(prompt_id, version).with_suffix(".tmp")
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return False

    def clear(self, prompt_id: str) -> None:
        """Remove all cached versions for *prompt_id*.  Never raises."""
        try:
            d = self._prompt_dir(prompt_id)
            if not d.is_dir():
                return
            for f in d.glob("*.json"):
                f.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug(
                "CacheManager.clear: error clearing %r: %s", prompt_id, exc
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _prune(self, prompt_id: str) -> None:
        """Remove the oldest versions beyond ``keep_versions``.

        Called automatically after every successful :meth:`write`.
        Failures are silently logged (CONSTRAINT #6 — never raises).
        """
        try:
            versions = self.list_versions(prompt_id)
            for version in versions[self._keep:]:
                path = self._record_path(prompt_id, version)
                path.unlink(missing_ok=True)
                logger.debug(
                    "CacheManager._prune: removed old version %r@%s", prompt_id, version
                )
        except Exception as exc:
            logger.debug(
                "CacheManager._prune: error pruning %r: %s", prompt_id, exc
            )
