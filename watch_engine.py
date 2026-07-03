"""
watch_engine.py — Symbol Watch with Threshold Alerts (Tier 1.4)
===============================================================
Configuration-driven rule engine that evaluates ``watch_rules.yaml`` rules
against the advisory pipeline output at the end of every ``run_once()`` cycle
and dispatches ntfy push notifications for matched rules.

Design principles
-----------------
* **Shift-adjusted / no-lookahead** (Gravity constraint): prior-run state is
  loaded BEFORE comparing against the just-completed run's output.  Alert
  decisions compare "previous action/conviction" vs. "current action/conviction".
  No market-data fetching, forecasting, or future-dated computation happens
  inside this module — it is pure comparison logic over already-computed
  advisory outputs.
* **Edge-triggered conviction alerts**: ``conviction_above`` and
  ``conviction_below`` fire only on the first run where the condition becomes
  true (the "rising" or "falling" edge), NOT on subsequent runs where the
  condition is already sustained.  This prevents ntfy notification spam when a
  symbol stays above a threshold for a week.
* **Dead-letter resilience** (CONSTRAINT #6): every per-rule evaluation is
  wrapped in try/except; one bad rule never aborts the rest.  No rules, missing
  YAML, or a fully empty recommendation list are all silent no-ops.
* **ntfy integration**: ``dispatch_watch_alerts`` calls ``alerting.notify()``,
  which is itself a no-op when ``NTFY_TOPIC`` is unset — so the entire engine
  is silently inert with zero configuration.
* **Secrets are never embedded in alert bodies** (CONSTRAINT #3).

Alert types
-----------
``action_change``
    Fires when the advisory action for a symbol changes between runs, e.g.
    HOLD → BUY or BUY → SELL.  Uses an edge trigger on the action string:
    fires exactly once per flip.  Does NOT fire on the very first run (no
    prior state to compare against).

``conviction_above``
    Fires on the first run where ``conviction ≥ threshold``, after the
    conviction was below it.  Subsequent runs where conviction stays above
    do NOT re-fire.  Resets (allows re-fire) when conviction drops back below.

``conviction_below``
    Mirror image — fires on the first run where ``conviction < threshold``
    after having been at or above it.

State file
----------
``output/watch_state.json`` — per-symbol JSON record written atomically
(write-then-rename) at the end of every pipeline cycle::

    {
      "AAPL": {
        "action": "BUY",
        "conviction": 0.82,
        "alerted_conviction_above": {"0.85": true},
        "alerted_conviction_below": {"0.50": false},
        "timestamp": "2026-06-26T10:00:00+00:00"
      }
    }

``alerted_conviction_above[tkey] = True`` means "conviction WAS ≥ threshold
on the most recent run".  The next run compares against this to detect the
rising edge (and prevents re-firing while the condition persists).
Missing file = first run with clean (empty) state.  Corrupt file = logged
at WARNING, clean state used, never aborts the pipeline.

watch_rules.yaml schema
-----------------------
::

    rules:
      - symbol: "*"            # ticker or "*" for all symbols in the universe
        alert_on: conviction_above
        threshold: 0.85        # required for conviction_above / conviction_below
        priority: high         # ntfy priority: min|low|default|high|urgent|max
        label: "High Conviction Alert"   # optional, used in notification title

      - symbol: AAPL
        alert_on: action_change
        priority: default
        label: "AAPL Action Change"

Integration in main.py
-----------------------
Called inside ``_run_cycle()`` after ``run_once()`` returns a ``RunResult``::

    rules  = load_watch_rules(settings.WATCH_RULES_FILE)
    prev   = load_watch_state(settings.OUTPUT_DIR / "watch_state.json")
    alerts, new_state = evaluate_watch_rules(rules, result.recommendations, prev)
    dispatch_watch_alerts(alerts, dashboard_url=os.environ.get("NTFY_DASHBOARD_URL"))
    save_watch_state(new_state, settings.OUTPUT_DIR / "watch_state.json")

The state update (``save_watch_state``) ALWAYS runs, even when no alerts fire,
so edge-trigger tracking advances correctly on quiet runs.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_VALID_PRIORITIES: frozenset[str] = frozenset(
    {"min", "low", "default", "high", "urgent", "max"}
)
_VALID_ALERT_TYPES: frozenset[str] = frozenset(
    {"action_change", "conviction_above", "conviction_below"}
)

# Maximum characters of rationale text to include in notification bodies.
_RATIONALE_MAX_CHARS: int = 100


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WatchRule:
    """One rule loaded from ``watch_rules.yaml``.

    Attributes
    ----------
    symbol :
        Ticker symbol (e.g. ``"AAPL"``) or ``"*"`` to match every symbol in
        the current recommendation universe.
    alert_on :
        Rule type: ``"action_change"``, ``"conviction_above"``, or
        ``"conviction_below"``.
    threshold :
        Required for conviction rules.  Ignored for ``action_change``.
        Must be in [0.0, 1.0].
    priority :
        ntfy push priority string.  Defaults to ``"default"``.
    label :
        Optional human-readable label used in the notification title.  Falls
        back to a sensible default when empty.
    """

    symbol: str
    alert_on: str
    threshold: Optional[float] = None
    priority: str = "default"
    label: str = ""


@dataclass(frozen=True)
class WatchAlert:
    """One fired alert, ready for dispatch via ``dispatch_watch_alerts()``.

    Attributes
    ----------
    symbol :       Ticker that triggered the rule.
    rule_type :    Same as ``WatchRule.alert_on``.
    priority :     ntfy priority string.
    title :        Short notification heading shown as the push title on mobile.
    message :      Notification body (kept under ~512 characters).
    trigger_detail : Machine-readable summary of the triggering condition.
    """

    symbol: str
    rule_type: str
    priority: str
    title: str
    message: str
    trigger_detail: str


@dataclass
class SymbolWatchState:
    """Per-symbol state persisted between pipeline runs.

    The edge-trigger logic depends on comparing current conviction/action
    against the values recorded here at the end of the **previous** run.

    Attributes
    ----------
    action :
        Last known advisory action (``"BUY"``, ``"HOLD"``, ``"SELL"``).
    conviction :
        Last known conviction score in [0, 1].
    alerted_conviction_above :
        Maps ``str(threshold)`` → bool.  ``True`` means conviction WAS ≥
        threshold on the most recent run (i.e. the condition was met last time).
        The next run uses this to determine whether the edge has already fired.
    alerted_conviction_below :
        Maps ``str(threshold)`` → bool.  ``True`` means conviction WAS <
        threshold on the most recent run.
    timestamp :
        ISO-8601 UTC string of the last update.
    """

    action: str = ""
    conviction: float = 0.0
    alerted_conviction_above: Dict[str, bool] = field(default_factory=dict)
    alerted_conviction_below: Dict[str, bool] = field(default_factory=dict)
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "action": self.action,
            "conviction": self.conviction,
            "alerted_conviction_above": dict(self.alerted_conviction_above),
            "alerted_conviction_below": dict(self.alerted_conviction_below),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SymbolWatchState":
        """Deserialise from a raw dict (e.g. parsed from JSON)."""
        return cls(
            action=str(d.get("action", "")),
            conviction=float(d.get("conviction", 0.0)),
            alerted_conviction_above=dict(d.get("alerted_conviction_above", {})),
            alerted_conviction_below=dict(d.get("alerted_conviction_below", {})),
            timestamp=str(d.get("timestamp", "")),
        )


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------


def load_watch_rules(path: "str | Path" = "watch_rules.yaml") -> List[WatchRule]:
    """Load watch rules from a YAML configuration file.

    Parameters
    ----------
    path :
        Path to the YAML file.  Defaults to ``"watch_rules.yaml"`` in the
        current working directory (matching ``settings.WATCH_RULES_FILE``).

    Returns
    -------
    list[WatchRule]
        Parsed, validated rules.  Returns ``[]`` when the file is missing,
        empty, or malformed — never raises.  Individual rules with unknown
        ``alert_on`` values or missing required fields are skipped with a
        WARNING log; all other rules in the file are still loaded.

    Notes
    -----
    Symbol values are normalised to uppercase.  Unknown ``priority`` strings
    fall back to ``"default"`` with a WARNING log.  Thresholds outside [0, 1]
    are rejected.
    """
    p = Path(path)
    if not p.exists():
        logger.debug(
            "watch_rules.yaml not found at '%s' — no watch rules active.", p
        )
        return []

    try:
        import yaml  # PyYAML — already in requirements.txt

        with p.open("r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except Exception as exc:
        logger.warning(
            "Failed to parse '%s': %s — no watch rules active.", p, exc
        )
        return []

    if not isinstance(doc, dict):
        logger.warning(
            "'%s' root is not a YAML mapping — no watch rules active.", p
        )
        return []

    raw_rules = doc.get("rules", [])
    if not isinstance(raw_rules, list):
        logger.warning(
            "'%s' top-level 'rules' key is not a list — no watch rules active.", p
        )
        return []

    parsed: List[WatchRule] = []
    for idx, r in enumerate(raw_rules):
        if not isinstance(r, dict):
            logger.warning("Rule #%d is not a mapping — skipped.", idx)
            continue

        # --- symbol (required) ---
        symbol_raw = str(r.get("symbol", "")).strip()
        if not symbol_raw:
            logger.warning("Rule #%d has no 'symbol' key — skipped.", idx)
            continue
        symbol = symbol_raw.upper()

        # --- alert_on (required, must be a known type) ---
        alert_on = str(r.get("alert_on", "")).strip().lower()
        if alert_on not in _VALID_ALERT_TYPES:
            logger.warning(
                "Rule #%d (symbol=%s) unknown alert_on=%r — valid values: %s — skipped.",
                idx, symbol, alert_on, sorted(_VALID_ALERT_TYPES),
            )
            continue

        # --- threshold (required for conviction rules) ---
        threshold: Optional[float] = None
        if alert_on in ("conviction_above", "conviction_below"):
            raw_t = r.get("threshold")
            if raw_t is None:
                logger.warning(
                    "Rule #%d (symbol=%s alert_on=%s) missing 'threshold' — skipped.",
                    idx, symbol, alert_on,
                )
                continue
            try:
                threshold = float(raw_t)
            except (TypeError, ValueError):
                logger.warning(
                    "Rule #%d (symbol=%s alert_on=%s) threshold=%r is not a "
                    "number — skipped.",
                    idx, symbol, alert_on, raw_t,
                )
                continue
            if not 0.0 <= threshold <= 1.0:
                logger.warning(
                    "Rule #%d (symbol=%s alert_on=%s) threshold=%.3f is outside "
                    "[0, 1] — skipped.",
                    idx, symbol, alert_on, threshold,
                )
                continue

        # --- priority (optional, defaults to "default") ---
        priority = str(r.get("priority", "default")).strip().lower()
        if priority not in _VALID_PRIORITIES:
            logger.warning(
                "Rule #%d (symbol=%s) unknown priority=%r; using 'default'.",
                idx, symbol, priority,
            )
            priority = "default"

        # --- label (optional) ---
        label = str(r.get("label", "")).strip()

        parsed.append(
            WatchRule(
                symbol=symbol,
                alert_on=alert_on,
                threshold=threshold,
                priority=priority,
                label=label,
            )
        )

    logger.info("Loaded %d watch rule(s) from '%s'.", len(parsed), p)
    return parsed


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_watch_state(path: Path) -> Dict[str, SymbolWatchState]:
    """Load per-symbol watch state from ``output/watch_state.json``.

    Parameters
    ----------
    path :
        Path to the state JSON file (typically
        ``settings.OUTPUT_DIR / "watch_state.json"``).

    Returns
    -------
    dict[str, SymbolWatchState]
        Per-symbol state, keyed by uppercase ticker.  Returns ``{}`` when the
        file is missing, empty, or corrupt — never raises (CONSTRAINT #6).
    """
    if not path.exists():
        logger.debug(
            "watch_state.json not found at '%s' — using empty initial state.", path
        )
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            logger.warning(
                "watch_state.json root is not a JSON object — using empty state."
            )
            return {}
        state: Dict[str, SymbolWatchState] = {}
        for sym, val in raw.items():
            if isinstance(val, dict):
                state[sym.upper()] = SymbolWatchState.from_dict(val)
        return state
    except Exception as exc:
        logger.warning(
            "Failed to load watch_state.json (%s) — using empty state.", exc
        )
        return {}


def save_watch_state(
    state: Dict[str, SymbolWatchState],
    path: Path,
) -> None:
    """Persist per-symbol watch state to disk atomically.

    Parameters
    ----------
    state :
        Dict mapping uppercase ticker → ``SymbolWatchState``.
    path :
        Destination file path.  Written via write-to-temp-then-rename so
        that a crash mid-write leaves the previous state intact.  Parent
        directory is created if absent.  Any write failure is logged as
        WARNING and silently ignored (CONSTRAINT #6 — the pipeline must
        never crash over a failed state write).
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {sym: st.to_dict() for sym, st in state.items()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.rename(path)
        logger.debug("watch_state.json written to '%s' (%d symbols).", path, len(state))
    except Exception as exc:
        logger.warning(
            "Failed to save watch_state.json to '%s' (%s) — state not persisted.",
            path, exc,
        )


# ---------------------------------------------------------------------------
# Alert body helpers
# ---------------------------------------------------------------------------


def _short_rationale(rationale: str, max_chars: int = _RATIONALE_MAX_CHARS) -> str:
    """Return the first sentence of a rationale string, capped at max_chars.

    Looks for the first sentence-terminal punctuation (``.``, ``!``, ``?``)
    within the first ``max_chars`` characters.  Falls back to truncation with
    a ``…`` suffix when none is found.
    """
    if not rationale:
        return ""
    for i, ch in enumerate(rationale[:max_chars]):
        if ch in ".!?":
            return rationale[: i + 1]
    if len(rationale) <= max_chars:
        return rationale
    return rationale[:max_chars].rstrip() + "…"


def _build_action_change_alert(
    rec: Any,
    prev_action: str,
    rule: WatchRule,
) -> WatchAlert:
    """Construct a ``WatchAlert`` for an ``action_change`` trigger.

    The alert body includes the symbol, the action transition, conviction
    score, suggested position size, and a brief rationale excerpt.
    """
    sym: str = rec.symbol
    curr_action: str = str(rec.action or "").upper()
    title = f"InvestYo 🔄 {sym}: {prev_action} → {curr_action}"
    body_lines = [
        f"{sym}: Action changed {prev_action} → {curr_action}",
        (
            f"Conviction: {rec.conviction:.2f}  "
            f"|  Position: {rec.suggested_position_pct * 100:.1f}%"
        ),
    ]
    rat = _short_rationale(getattr(rec, "rationale", "") or "")
    if rat:
        body_lines.append(f"Rationale: {rat}")
    return WatchAlert(
        symbol=sym,
        rule_type="action_change",
        priority=rule.priority,
        title=title,
        message="\n".join(body_lines),
        trigger_detail=f"{prev_action} → {curr_action}",
    )


def _build_conviction_alert(
    rec: Any,
    threshold: float,
    direction: str,
    rule: WatchRule,
) -> WatchAlert:
    """Construct a ``WatchAlert`` for a ``conviction_above`` or ``conviction_below`` trigger.

    Parameters
    ----------
    direction : ``"above"`` or ``"below"``.
    """
    sym: str = rec.symbol
    emoji = "🎯" if direction == "above" else "⬇"
    cmp_str = "≥" if direction == "above" else "<"
    title = (
        f"InvestYo {emoji} {sym}: "
        f"Conviction {rec.conviction:.2f} {cmp_str} {threshold}"
    )
    body_lines = [
        f"{sym}: Conviction {rec.conviction:.3f} {cmp_str} threshold {threshold}",
        (
            f"Action: {rec.action}  "
            f"|  Position: {rec.suggested_position_pct * 100:.1f}%"
        ),
    ]
    rat = _short_rationale(getattr(rec, "rationale", "") or "")
    if rat:
        body_lines.append(f"Rationale: {rat}")
    return WatchAlert(
        symbol=sym,
        rule_type=f"conviction_{direction}",
        priority=rule.priority,
        title=title,
        message="\n".join(body_lines),
        trigger_detail=(
            f"conviction={rec.conviction:.3f} {cmp_str} threshold={threshold}"
        ),
    )


# ---------------------------------------------------------------------------
# Core evaluation engine
# ---------------------------------------------------------------------------


def evaluate_watch_rules(
    rules: List[WatchRule],
    recommendations: List[Any],
    prev_state: Dict[str, SymbolWatchState],
) -> Tuple[List[WatchAlert], Dict[str, SymbolWatchState]]:
    """Evaluate all watch rules against the current run's recommendations.

    This function embodies the no-lookahead contract:

    * ``prev_state`` contains data from the END of the **previous** run.
    * ``recommendations`` is the advisory output from the **just-completed**
      run (already causal — all indicator/forecast computation is finished).
    * The resulting ``new_state`` is derived solely from ``recommendations``
      and is saved AFTER alerts fire, becoming ``prev_state`` on the next call.
    * No market-data fetching, model inference, or future-dated computation
      occurs here — this is pure comparison logic over already-computed outputs.

    Parameters
    ----------
    rules :
        Parsed ``WatchRule`` objects from ``load_watch_rules()``.
    recommendations :
        Current run's advisory outputs, duck-typed: each object must expose
        ``.symbol``, ``.action``, ``.conviction``, ``.suggested_position_pct``,
        and ``.rationale`` attributes.
    prev_state :
        Per-symbol state from the previous run.  Pass ``{}`` on the first run.

    Returns
    -------
    (alerts, new_state)
        ``alerts``    — ``WatchAlert`` list ready for ``dispatch_watch_alerts()``.
        ``new_state`` — Updated per-symbol state for ``save_watch_state()``.
    """
    now_ts = datetime.now(timezone.utc).isoformat()

    # Build base new_state from current recommendations.
    # alerted_conviction_above/below are populated below as rules are evaluated.
    new_state: Dict[str, SymbolWatchState] = {}
    for rec in recommendations:
        sym = rec.symbol.upper()
        new_state[sym] = SymbolWatchState(
            action=str(rec.action or "").upper(),
            conviction=float(rec.conviction),
            alerted_conviction_above={},
            alerted_conviction_below={},
            timestamp=now_ts,
        )

    if not rules:
        # No rules configured → nothing to evaluate; still return updated state
        # so the file advances on every run (keeps timestamps fresh).
        return [], new_state

    # Index current recommendations by uppercase symbol for O(1) lookup.
    rec_by_sym: Dict[str, Any] = {r.symbol.upper(): r for r in recommendations}

    alerts: List[WatchAlert] = []

    for rule in rules:
        try:
            # Determine which symbols this rule applies to.
            if rule.symbol == "*":
                target_symbols: List[str] = list(rec_by_sym.keys())
            else:
                target_symbols = [rule.symbol] if rule.symbol in rec_by_sym else []

            for sym in target_symbols:
                rec = rec_by_sym[sym]
                ps: SymbolWatchState = prev_state.get(sym, SymbolWatchState())
                ns: SymbolWatchState = new_state[sym]

                if rule.alert_on == "action_change":
                    _evaluate_action_change(rec, ps, rule, alerts)

                elif rule.alert_on == "conviction_above":
                    # threshold is guaranteed non-None by load_watch_rules validation
                    assert rule.threshold is not None
                    _evaluate_conviction_above(rec, ps, rule, ns, alerts)

                elif rule.alert_on == "conviction_below":
                    assert rule.threshold is not None
                    _evaluate_conviction_below(rec, ps, rule, ns, alerts)

        except Exception as exc:
            # Dead-letter pattern (CONSTRAINT #6): one broken rule must not
            # prevent other rules from firing or the pipeline from continuing.
            logger.warning(
                "Watch rule evaluation error (symbol=%s alert_on=%s): %s — skipped.",
                rule.symbol,
                rule.alert_on,
                exc,
            )

    return alerts, new_state


def _evaluate_action_change(
    rec: Any,
    ps: SymbolWatchState,
    rule: WatchRule,
    alerts: List[WatchAlert],
) -> None:
    """Append an action_change alert if the advisory action flipped since last run.

    No alert is emitted on the very first run for a symbol (``ps.action == ""``)
    because there is no prior action to compare against.
    """
    curr_action = str(rec.action or "").upper()
    prev_action = str(ps.action or "").upper()
    if prev_action and curr_action != prev_action:
        alerts.append(_build_action_change_alert(rec, prev_action, rule))


def _evaluate_conviction_above(
    rec: Any,
    ps: SymbolWatchState,
    rule: WatchRule,
    ns: SymbolWatchState,
    alerts: List[WatchAlert],
) -> None:
    """Edge-trigger: fires once when conviction crosses ABOVE threshold.

    Updates ``ns.alerted_conviction_above[tkey]`` to reflect the current
    state so the next call can detect the edge correctly.
    """
    tkey = str(rule.threshold)
    was_above: bool = ps.alerted_conviction_above.get(tkey, False)
    is_above: bool = float(rec.conviction) >= rule.threshold  # type: ignore[arg-type]
    if is_above and not was_above:
        alerts.append(_build_conviction_alert(rec, rule.threshold, "above", rule))  # type: ignore[arg-type]
    # Update new state regardless — tracks the current condition for next run.
    ns.alerted_conviction_above[tkey] = is_above


def _evaluate_conviction_below(
    rec: Any,
    ps: SymbolWatchState,
    rule: WatchRule,
    ns: SymbolWatchState,
    alerts: List[WatchAlert],
) -> None:
    """Edge-trigger: fires once when conviction crosses BELOW threshold.

    Updates ``ns.alerted_conviction_below[tkey]`` to reflect the current
    state so the next call can detect the edge correctly.
    """
    tkey = str(rule.threshold)
    was_below: bool = ps.alerted_conviction_below.get(tkey, False)
    is_below: bool = float(rec.conviction) < rule.threshold  # type: ignore[arg-type]
    if is_below and not was_below:
        alerts.append(_build_conviction_alert(rec, rule.threshold, "below", rule))  # type: ignore[arg-type]
    ns.alerted_conviction_below[tkey] = is_below


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch_watch_alerts(
    alerts: List[WatchAlert],
    *,
    dashboard_url: Optional[str] = None,
) -> None:
    """Dispatch fired alerts as ntfy push notifications.

    Calls ``alerting.notify()`` for each alert.  When ``NTFY_TOPIC`` is unset
    ``notify()`` is already a no-op, so this function is silently inert with
    no ntfy configuration.  Each per-alert dispatch is individually wrapped in
    try/except — a network failure on one alert never blocks the rest
    (CONSTRAINT #6).

    Parameters
    ----------
    alerts :
        ``WatchAlert`` objects to dispatch; empty list is a silent no-op.
    dashboard_url :
        Optional deep-link URL (e.g. ``"http://localhost:8501"``) appended to
        each notification body so the operator can jump to the dashboard.
        Read from the ``NTFY_DASHBOARD_URL`` environment variable by the
        caller (``main._run_cycle``).  Secrets must NOT be embedded here.
    """
    if not alerts:
        return

    # Import inline to avoid a module-level circular dependency and to allow
    # tests to monkeypatch alerting.notify cleanly (the import happens fresh
    # on every call so the mock is resolved at dispatch time).
    from alerting import notify  # noqa: PLC0415

    for alert in alerts:
        try:
            msg = alert.message
            # Tier 9 — append (never replace) LLM-generated commentary when
            # the master switch is on AND a key is configured.  Soft-fail
            # contract: enrich is None → msg is the unchanged template.
            # CONSTRAINT #4 + #6.  Lazy import keeps the SDK reach off the
            # module's top level.
            if getattr(settings, "LLM_COMMENTARY_ENABLED", False):
                try:
                    from llm.commentary import generate_alert_commentary  # noqa: PLC0415

                    enrich = generate_alert_commentary(
                        alert_skeleton={
                            "symbol": alert.symbol,
                            "rule_type": alert.rule_type,
                            "priority": alert.priority,
                            "trigger_detail": alert.trigger_detail,
                            "template": alert.message,
                        },
                        context={},
                    )
                    if enrich is not None:
                        msg = f"{msg}\n\n📝 {enrich.body}"
                except Exception as exc:
                    logger.debug(
                        "LLM watch-alert augmentation soft-failed for %s: %s",
                        alert.symbol,
                        exc,
                    )
            if dashboard_url:
                msg = f"{msg}\n\n📊 Dashboard: {dashboard_url}"
            notify(title=alert.title, message=msg, priority=alert.priority)
            logger.info(
                "Watch alert dispatched — symbol=%s rule=%s detail=%s",
                alert.symbol,
                alert.rule_type,
                alert.trigger_detail,
            )
        except Exception as exc:
            logger.warning(
                "Failed to dispatch watch alert for %s (%s): %s",
                alert.symbol,
                alert.rule_type,
                exc,
            )
