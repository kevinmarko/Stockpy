"""
gui/strategy_registry.py — strategy/module version tracking + global mode toggle.

Two responsibilities — both deliberately kept out of ``gui/panels.py`` so the
Streamlit code stays declarative and these surfaces are unit-testable cold.

1. **Strategy file versioning.** ``list_strategy_versions()`` walks the
   ``signals/`` package, hashes each module file (sha256, first 12 hex chars),
   reads its mtime, and joins that with the live ``signals.registry`` registry
   so the Strategy Matrix tab can show:

       module                  enabled  weight   version   last modified
       timeseries_momentum     ✅       45.0     a1b2c3d4  2026-06-26 08:40

   This is "version" in the operational sense — was a strategy file
   touched since the last orchestrator launch — not semver. That's what
   the operator actually wants when triaging "did I really redeploy this
   strategy when I think I did?"

2. **Global Paper / Live mode toggle.** ``read_active_mode()`` synthesises the
   current execution mode from ``settings.ALPACA_PAPER`` and
   ``settings.DRY_RUN`` (the existing source of truth). ``set_active_mode``
   writes both env vars via the allowlist-bounded :mod:`gui.env_io` so the
   GUI cannot accidentally enable live trading without explicitly flipping
   ``ALPACA_PAPER`` to ``false``. **The setting takes effect on the next
   orchestrator launch** — we never patch a running process.

Constraints honoured
--------------------
* CONSTRAINT #3 (env-only writes via allowlist) — ``ALPACA_PAPER`` and
  ``DRY_RUN`` are both writable via :mod:`gui.env_io`.
* CONSTRAINT #5 (never fabricate) — a module without a registered file path
  returns ``version=None`` rather than an empty/synthetic hash.
* CONSTRAINT #6 (dead-letter) — hashing failures degrade to ``version=None``
  with a logged warning; the panel still renders.
* CONSTRAINT #9 (type hints) on every public function.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

_HASH_PREFIX_LEN = 12


# ---------------------------------------------------------------------------
# Strategy version dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StrategyVersion:
    """One signal module's deployment fingerprint.

    Attributes
    ----------
    name:
        Registered module key (``signals.registry.global_registry`` lookup).
    file_path:
        Resolved path of the module's ``.py`` file, or ``None`` for modules
        defined inline (no source file we can hash).
    version_hash:
        First ``_HASH_PREFIX_LEN`` hex chars of the file's sha256. ``None``
        when the file is missing / unreadable.
    last_modified:
        File mtime as a UTC-aware datetime. ``None`` when the file is missing.
    enabled:
        ``True`` when the module is NOT in ``settings.DISABLED_SIGNAL_MODULES``.
    weight:
        ``settings.SIGNAL_WEIGHTS[name]`` or 0.0 when absent.
    """

    name: str
    file_path: Optional[Path]
    version_hash: Optional[str]
    last_modified: Optional[datetime]
    enabled: bool
    weight: float


def _hash_file(path: Path) -> Optional[str]:
    """Return ``sha256(path)[:_HASH_PREFIX_LEN]`` hex. ``None`` on read failure."""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65_536), b""):
                digest.update(chunk)
        return digest.hexdigest()[:_HASH_PREFIX_LEN]
    except OSError as exc:
        logger.warning("strategy_registry: failed to hash %s: %s", path, exc)
        return None


def _file_mtime(path: Path) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def list_strategy_versions(
    *,
    module_names: Optional[List[str]] = None,
    weights: Optional[Mapping[str, float]] = None,
    disabled: Optional[Mapping[str, bool] | List[str]] = None,
    signals_dir: Optional[Path] = None,
) -> List[StrategyVersion]:
    """Return :class:`StrategyVersion` records for every known signal module.

    Parameters
    ----------
    module_names:
        Optional explicit module list. When ``None``, the function imports
        :mod:`signals.registry` and enumerates the live registry.
    weights:
        Override for ``settings.SIGNAL_WEIGHTS``. When ``None`` the live
        settings are read.
    disabled:
        Override for ``settings.DISABLED_SIGNAL_MODULES``. Accepts a mapping
        ``{name: bool}`` (True == disabled) or a list of disabled names.
    signals_dir:
        Override for the ``signals/`` directory. Default ``<repo>/signals``.

    Notes
    -----
    The function never raises on a missing module file or weight — those
    fields degrade to ``None``/``0.0`` so the panel can still render every
    registered module.
    """
    repo_root = Path(__file__).resolve().parent.parent
    signals_dir = signals_dir or (repo_root / "signals")

    if module_names is None:
        try:
            from signals.registry import global_registry  # noqa: WPS433
            import signals as _signals  # noqa: F401, WPS433 — triggers registration
            module_names = sorted(global_registry.get_all().keys())
        except Exception as exc:  # noqa: BLE001 — degraded mode
            logger.warning(
                "strategy_registry: registry unavailable (%s); "
                "falling back to settings.SIGNAL_WEIGHTS",
                exc,
            )
            module_names = []

    if weights is None:
        try:
            from settings import settings as _settings  # noqa: WPS433
            weights = dict(_settings.SIGNAL_WEIGHTS)
            if not module_names:
                module_names = sorted(weights.keys())
        except Exception as exc:  # noqa: BLE001
            logger.warning("strategy_registry: settings load failed (%s)", exc)
            weights = {}

    if disabled is None:
        try:
            from settings import settings as _settings  # noqa: WPS433
            disabled = list(_settings.DISABLED_SIGNAL_MODULES)
        except Exception:  # noqa: BLE001 — degraded mode
            disabled = []

    if isinstance(disabled, Mapping):
        disabled_set = {k for k, v in disabled.items() if v}
    else:
        disabled_set = set(disabled)

    records: List[StrategyVersion] = []
    for name in module_names:
        candidate = signals_dir / f"{name}.py"
        file_path: Optional[Path] = candidate if candidate.exists() else None
        version_hash = _hash_file(file_path) if file_path else None
        mtime = _file_mtime(file_path) if file_path else None
        records.append(StrategyVersion(
            name=name,
            file_path=file_path,
            version_hash=version_hash,
            last_modified=mtime,
            enabled=(name not in disabled_set),
            weight=float(weights.get(name, 0.0)),
        ))
    return records


# ---------------------------------------------------------------------------
# Paper / Live global mode toggle
# ---------------------------------------------------------------------------

class ExecutionMode(str, Enum):
    """Operational mode for the platform's order pipeline.

    * ``PAPER`` — ``ALPACA_PAPER=true`` (broker traffic to the paper sandbox).
    * ``LIVE``  — ``ALPACA_PAPER=false`` (broker traffic to the live endpoint).
    * ``SIMULATION`` — ``DRY_RUN=true`` regardless of ALPACA_PAPER.  The
      OrderManager intercepts every intent before any broker contact.
    """

    SIMULATION = "simulation"
    PAPER = "paper"
    LIVE = "live"

    @property
    def label(self) -> str:
        return {
            ExecutionMode.SIMULATION: "🧪 Simulation (DRY_RUN)",
            ExecutionMode.PAPER:      "📝 Paper trading",
            ExecutionMode.LIVE:       "🔴 Live production",
        }[self]


@dataclass(frozen=True)
class ModeState:
    """Resolved execution mode + the underlying env-var flags."""

    mode: ExecutionMode
    alpaca_paper: bool
    dry_run: bool

    @property
    def is_live(self) -> bool:
        return self.mode is ExecutionMode.LIVE


def read_active_mode() -> ModeState:
    """Resolve the current :class:`ExecutionMode` from settings.

    Order of precedence:

    1. ``DRY_RUN=true`` → :data:`ExecutionMode.SIMULATION` regardless of
       ``ALPACA_PAPER`` (because OrderManager intercepts before broker
       contact).
    2. Otherwise ``ALPACA_PAPER`` decides PAPER (``True``) vs LIVE (``False``).
    """
    try:
        from settings import settings as _settings  # noqa: WPS433
        alpaca_paper = bool(_settings.ALPACA_PAPER)
        dry_run = bool(_settings.DRY_RUN)
    except Exception as exc:  # noqa: BLE001
        logger.warning("read_active_mode: settings load failed (%s); assuming SIMULATION", exc)
        return ModeState(ExecutionMode.SIMULATION, alpaca_paper=True, dry_run=True)

    if dry_run:
        mode = ExecutionMode.SIMULATION
    elif alpaca_paper:
        mode = ExecutionMode.PAPER
    else:
        mode = ExecutionMode.LIVE
    return ModeState(mode=mode, alpaca_paper=alpaca_paper, dry_run=dry_run)


def set_active_mode(mode: ExecutionMode | str) -> ModeState:
    """Persist a new :class:`ExecutionMode` to ``.env`` via :mod:`gui.env_io`.

    Writes the **two** env vars that together define the mode:

    * ``DRY_RUN``       — ``true`` only for SIMULATION.
    * ``ALPACA_PAPER``  — ``true`` for SIMULATION + PAPER; ``false`` for LIVE.

    The change takes effect on the next orchestrator launch — we do NOT
    monkey-patch a running ``settings.Settings`` instance, because mid-run
    mode flips would inevitably create order-routing race conditions.

    Raises
    ------
    ValueError
        If ``mode`` is not a valid :class:`ExecutionMode` string/value.
    SecretWriteError / DisallowedKeyError
        Propagated from :mod:`gui.env_io` if either env var is not in the
        allowlist.
    """
    if isinstance(mode, str):
        try:
            mode = ExecutionMode(mode.lower())
        except ValueError as exc:
            raise ValueError(
                f"Invalid mode {mode!r}; expected one of "
                f"{[m.value for m in ExecutionMode]}"
            ) from exc

    dry_run = (mode is ExecutionMode.SIMULATION)
    alpaca_paper = (mode is not ExecutionMode.LIVE)

    from gui import env_io  # local import keeps the module import-light
    env_io.write_setting("DRY_RUN", dry_run)
    env_io.write_setting("ALPACA_PAPER", alpaca_paper)

    return ModeState(mode=mode, alpaca_paper=alpaca_paper, dry_run=dry_run)


def mode_banner_text(state: ModeState) -> str:
    """Return the one-line banner string for the Strategy Matrix tab."""
    return f"Active mode: {state.mode.label}  •  ALPACA_PAPER={state.alpaca_paper}  •  DRY_RUN={state.dry_run}"
