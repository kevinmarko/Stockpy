"""execution/compose.py
=========================
Advisory queue COMPOSER — the single writer of ``output/execution_queue.json``.

Why this exists
----------------
The advisory pipeline (``main.py``, every cycle) writes its own small source
file (``output/queue_sources/advisory.json``) and then calls
:func:`compose_and_emit`, which reads it back, GATES the result through the
existing risk pipeline, and EMITS one queue via
``execution.queue_builder.emit_execution_queue`` — unchanged, zero new order
-submission code (this file is on the AST guard's manual scan-target list
because it touches order sizing; see
``tests/test_pipeline_smoke.py::TestNoOrderFunctions._EXECUTION_ZONE_GUARDED_FILES``).

History: this module used to union the advisory source with one source per
actively-followed Pilot (``follow-<pilot_id>``) and net overlapping claims.
Follow-a-Pilot was archived to ``legacy/`` in 2026-09 (step 4c), after every
follow had been cancelled, so only the advisory source remains. The queue it
writes is byte-identical to the pre-archive output for advisory-only input —
pinned by ``tests/test_compose_advisory_only_golden.py``.

Source file schema (``output/queue_sources/advisory.json``)
-------------------------------------------------------------
::

    {
      "schema_version": 1,
      "source_id": "advisory",
      "generated_at": "<ISO-8601 UTC>",
      "targets": [...]
    }

Targets carry the same fields ``execution.queue_builder._intent_dict``
already reads off a ``Recommendation`` (``symbol``, ``action``,
``conviction``, ``suggested_position_pct``, ``strategy``, ``rationale``) — a
RAW, unfiltered record of every actionable advisory recommendation that cycle
(conviction filtering happens at compose time, not write time, so a later
config change is honored without needing to rewrite the source).

Honesty / dead-letter posture (CONSTRAINT #4 / #6)
-----------------------------------------------------
* A MISSING source file is a legitimate state (no advisory cycle has written
  one yet); nothing is composed and the queue is left alone.
* A CORRUPT (unparseable) or STALE (``generated_at`` older than
  ``settings.QUEUE_SOURCE_MAX_AGE_SECONDS``) source is data we cannot trust.
  The correct response is to refuse the compose call (write nothing, leave the
  previously-emitted ``execution_queue.json`` untouched) and log loudly.
* This module never contacts a broker and defines no order-submission
  function (see the AST guard note above).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "SOURCE_SCHEMA_VERSION",
    "ADVISORY_SOURCE_ID",
    "SourceReadResult",
    "read_source",
    "write_source",
    "write_advisory_source",
    "AdvisorySourceClaims",
    "ComposedIntent",
    "compose_targets",
    "compose_and_emit",
]

SOURCE_SCHEMA_VERSION = 1
ADVISORY_SOURCE_ID = "advisory"
_SOURCE_DIR_NAME = "queue_sources"


def _coerce_float(value: Any) -> Optional[float]:
    """Coerce to a finite float, or ``None`` when not possible."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


def _source_dir(output_dir: Optional[Any]) -> Path:
    if output_dir is None:
        from settings import settings
        output_dir = settings.OUTPUT_DIR
    return Path(output_dir) / _SOURCE_DIR_NAME


def _source_path(output_dir: Optional[Any], source_id: str) -> Path:
    return _source_dir(output_dir) / f"{source_id}.json"


# ---------------------------------------------------------------------------
# Source file I/O
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceReadResult:
    """Result of :func:`read_source`.

    ``present`` false + ``corrupt`` false + ``stale`` false means "this
    source legitimately doesn't exist yet" -- the honest, non-error case.
    ``corrupt`` or ``stale`` true means "data exists but cannot be trusted";
    callers MUST refuse to compose (see module docstring).
    """

    source_id: str
    present: bool
    corrupt: bool
    stale: bool
    generated_at: Optional[datetime]
    targets: List[Dict[str, Any]]


def read_source(
    source_id: str,
    *,
    output_dir: Optional[Any] = None,
    max_age_seconds: Optional[float] = None,
    now: Optional[datetime] = None,
) -> SourceReadResult:
    """Read one ``queue_sources/<source_id>.json`` file.

    Never raises. A missing file yields ``present=False`` (not an error).
    An unparseable file, or one whose top-level shape is wrong, yields
    ``corrupt=True``. A parseable-but-too-old file yields ``stale=True``
    when ``max_age_seconds`` is given and exceeded.
    """
    now = now or datetime.now(timezone.utc)
    path = _source_path(output_dir, source_id)
    if not path.exists():
        return SourceReadResult(
            source_id=source_id, present=False, corrupt=False, stale=False,
            generated_at=None, targets=[],
        )
    corrupt = SourceReadResult(
        source_id=source_id, present=True, corrupt=True, stale=False,
        generated_at=None, targets=[],
    )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("compose: %s is unreadable/corrupt (%s)", path, exc)
        return corrupt
    if not isinstance(raw, dict):
        logger.warning("compose: %s is not a JSON object; treated as corrupt", path)
        return corrupt

    targets = raw.get("targets")
    if not isinstance(targets, list):
        targets = []

    generated_at: Optional[datetime] = None
    gen_raw = raw.get("generated_at")
    if isinstance(gen_raw, str):
        try:
            generated_at = datetime.fromisoformat(gen_raw)
        except ValueError:
            logger.warning("compose: %s has an unparseable generated_at (%r); treated as corrupt",
                           path, gen_raw)
            return corrupt
    if generated_at is None:
        logger.warning("compose: %s is missing generated_at; treated as corrupt", path)
        return corrupt

    stale = False
    if max_age_seconds is not None and max_age_seconds > 0:
        age = (now - generated_at).total_seconds()
        stale = age > max_age_seconds

    return SourceReadResult(
        source_id=source_id, present=True, corrupt=False, stale=stale,
        generated_at=generated_at,
        targets=[t for t in targets if isinstance(t, dict)],
    )


def write_source(
    source_id: str,
    targets: List[Dict[str, Any]],
    *,
    output_dir: Optional[Any] = None,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Atomically write one ``queue_sources/<source_id>.json`` file
    (write-then-rename, matching ``execution/kill_switch.py``'s idiom).

    Never raises: a write failure is logged and swallowed, returning
    ``None`` (CONSTRAINT #6) -- a source-write failure must not crash the
    caller (``main.py``), it just means this source stays at its previous
    state (or absent) until the next successful write.
    """
    now = now or datetime.now(timezone.utc)
    try:
        directory = _source_dir(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{source_id}.json"
        payload = {
            "schema_version": SOURCE_SCHEMA_VERSION,
            "source_id": source_id,
            "generated_at": now.isoformat(),
            "targets": targets or [],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path
    except Exception as exc:
        logger.warning("compose: failed to write source %s (%s)", source_id, exc)
        return None


def write_advisory_source(
    recommendations: Any,
    *,
    output_dir: Optional[Any] = None,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Write the advisory source from ``main.py``'s ``RunResult.recommendations``.

    A RAW, unfiltered record of every actionable (BUY/SELL) recommendation --
    conviction filtering happens at compose time (against the LIVE
    ``queue_builder.CONFIG["min_conviction"]``), not here, so a later config
    change is honored without needing to rewrite this file.
    """
    now = now or datetime.now(timezone.utc)
    targets: List[Dict[str, Any]] = []
    for rec in recommendations or []:
        try:
            action = str(getattr(rec, "action", "")).upper()
            symbol = str(getattr(rec, "symbol", "")).upper().strip()
            if not symbol or action not in ("BUY", "SELL"):
                continue
            targets.append({
                "symbol": symbol,
                "action": action,
                "conviction": _coerce_float(getattr(rec, "conviction", 0.0)) or 0.0,
                "suggested_position_pct": _coerce_float(getattr(rec, "suggested_position_pct", 0.0)) or 0.0,
                "strategy": str(getattr(rec, "strategy", "")),
                "rationale": str(getattr(rec, "rationale", "") or getattr(rec, "strategy", "")),
            })
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("compose: skipping advisory rec for %s (%s)", getattr(rec, "symbol", "?"), exc)
    return write_source(ADVISORY_SOURCE_ID, targets, output_dir=output_dir, now=now)


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdvisorySourceClaims:
    targets: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ComposedIntent:
    """The composer's own lightweight, ``getattr``-readable rec shape --
    everything ``execution.queue_builder._intent_dict`` reads off a
    ``Recommendation``, plus the additive ``sources`` attribution field.
    (``queue_builder`` still emits an ``overridden`` key; with advisory as the
    only source nothing can be overridden, so it is always ``[]``.)"""

    symbol: str
    action: str
    strategy: str
    conviction: float
    suggested_position_pct: float
    target_notional: float
    rationale: str
    strategy_id: str
    sources: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class _ComposedRunResult:
    """``RunResult``-shaped shim -- ``execution.queue_builder`` reads
    ``.recommendations``, ``.snapshot``, and ``.macro_dto`` via ``getattr``."""

    recommendations: List[ComposedIntent] = field(default_factory=list)
    snapshot: Any = None
    macro_dto: Any = None


def _advisory_min_conviction() -> float:
    try:
        from execution.queue_builder import CONFIG as _QB_CONFIG
        return float(_QB_CONFIG.get("min_conviction", 0.85))
    except Exception:  # pragma: no cover - defensive
        return 0.85


def _advisory_intent(rec: Dict[str, Any], equity: float) -> Optional[ComposedIntent]:
    symbol = str(rec.get("symbol") or "").upper().strip()
    action = str(rec.get("action") or "").upper()
    if not symbol or action not in ("BUY", "SELL"):
        return None
    conviction = _coerce_float(rec.get("conviction")) or 0.0
    suggested_pct = _coerce_float(rec.get("suggested_position_pct")) or 0.0
    strategy = str(rec.get("strategy") or "")
    rationale = str(rec.get("rationale") or strategy)
    # target_notional here is INFORMATIONAL ONLY (for the `sources` metadata)
    # -- the actual order sizing is recomputed downstream by
    # queue_builder._intent_dict from action/suggested_position_pct; the
    # composer never touches it.
    own_notional = round(float(suggested_pct) * equity, 2) if action == "BUY" else 0.0
    return ComposedIntent(
        symbol=symbol, action=action, strategy=strategy, conviction=conviction,
        suggested_position_pct=suggested_pct, target_notional=own_notional,
        rationale=rationale, strategy_id=ADVISORY_SOURCE_ID,
        sources=[{"source_id": ADVISORY_SOURCE_ID, "target_notional": own_notional}],
    )


def compose_targets(
    *,
    advisory: Optional[AdvisorySourceClaims],
    account_snapshot: Any,
) -> List[ComposedIntent]:
    """Filter the advisory targets to actionable, above-floor claims (first
    claim per symbol wins) and shape them for ``queue_builder``. Pure (no
    I/O, no gating) -- see :func:`compose_and_emit` for the full pipeline.

    Returns ``[]`` when the account snapshot has no positive ``total_equity``.
    """
    equity = _coerce_float(getattr(account_snapshot, "total_equity", None))
    if equity is None or equity <= 0:
        return []

    min_conviction = _advisory_min_conviction()
    advisory_claims: Dict[str, Dict[str, Any]] = {}
    for t in (advisory.targets if advisory is not None else []):
        try:
            symbol = str(t.get("symbol") or "").upper().strip()
            action = str(t.get("action") or "").upper()
            if not symbol or action not in ("BUY", "SELL"):
                continue
            conviction = _coerce_float(t.get("conviction")) or 0.0
            if conviction < min_conviction:
                continue
            if symbol in advisory_claims:
                continue  # first one wins; duplicates within one cycle shouldn't happen
            advisory_claims[symbol] = t
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("compose: skipping malformed advisory target (%s)", exc)

    results: List[ComposedIntent] = []
    for symbol in sorted(advisory_claims):
        ci = _advisory_intent(advisory_claims[symbol], equity)
        if ci is not None:
            results.append(ci)
    return results


def compose_and_emit(
    account_snapshot: Any,
    *,
    output_dir: Optional[Any] = None,
    mode: Optional[str] = None,
    now: Optional[datetime] = None,
    max_age_seconds: Optional[float] = None,
    macro_dto: Optional[Any] = None,
) -> Optional[Path]:
    """Read the advisory source, compose, gate, and emit ONE
    ``execution_queue.json``. ``main.py`` calls this right after
    :func:`write_advisory_source`.

    ``macro_dto``, when supplied (``main.py`` passes its own cycle's
    ``result.macro_dto``), is threaded through to the risk gate so
    ``PreTradeRiskGate``'s macro checks (macro kill switch, stress scenario,
    HMM regime) genuinely evaluate real VIX/Sahm/regime state instead of
    unconditionally passing. When not supplied, falls back to a zero-network
    cache read via ``execution.macro_snapshot.load_cached_macro_dto`` rather
    than leaving the gate permanently blind — see that module's docstring for
    the fail-open-but-audible contract when nothing is cached yet.

    Returns the written ``Path``, or ``None`` when: the execution mode is
    ``off`` (nothing written, matching ``emit_execution_queue``'s own
    contract), the advisory source is corrupt or stale (leaves the last queue
    in place — see module docstring), nothing is composable, or the account
    snapshot has no positive equity. Never raises (CONSTRAINT #6).
    """
    now = now or datetime.now(timezone.utc)
    if output_dir is None:
        from settings import settings
        output_dir = settings.OUTPUT_DIR
    output_dir = Path(output_dir)

    resolved_macro = macro_dto
    if resolved_macro is None:
        try:
            from execution.macro_snapshot import load_cached_macro_dto
            resolved_macro = load_cached_macro_dto(now=now)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("compose: cached-macro fallback unavailable (%s)", exc)

    if max_age_seconds is None:
        try:
            from settings import settings
            max_age_seconds = float(settings.QUEUE_SOURCE_MAX_AGE_SECONDS)
        except Exception:  # pragma: no cover - defensive
            max_age_seconds = 604800.0

    try:
        advisory_read = read_source(
            ADVISORY_SOURCE_ID, output_dir=output_dir,
            max_age_seconds=max_age_seconds, now=now,
        )
        if advisory_read.corrupt or advisory_read.stale:
            logger.warning(
                "compose: refusing to compose -- advisory source corrupt=%s stale=%s "
                "(generated_at=%s); leaving the existing execution_queue.json untouched",
                advisory_read.corrupt, advisory_read.stale, advisory_read.generated_at,
            )
            return None

        advisory_claims = (
            AdvisorySourceClaims(targets=advisory_read.targets) if advisory_read.present else None
        )
        composed = compose_targets(advisory=advisory_claims, account_snapshot=account_snapshot)
        if not composed:
            # Nothing to write -- avoid unnecessary queue churn.
            return None

        run_result = _ComposedRunResult(
            recommendations=composed, snapshot=account_snapshot, macro_dto=resolved_macro,
        )
        from execution.queue_builder import emit_execution_queue
        # min_conviction 0.0 at the queue_builder stage: the advisory floor
        # (queue_builder.CONFIG["min_conviction"]) has already been applied in
        # compose_targets above, so a second filter here would be a no-op.
        # Kept as a literal (it was Follow-a-Pilot's Decision-D3 floor before
        # step 4c) so the emitted queue stays byte-identical.
        return emit_execution_queue(
            run_result, mode=mode, output_dir=output_dir,
            config={"strategy_id": "composed", "min_conviction": 0.0}, now=now,
            macro_dto=resolved_macro,
        )
    except Exception as exc:  # pragma: no cover - belt-and-suspenders dead-letter
        logger.warning("compose: compose_and_emit failed (%s); execution_queue.json untouched", exc)
        return None
