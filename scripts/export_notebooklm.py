"""Automated Markdown export script for Google NotebookLM ingestion.

Formats the platform's current state into a modular multi-source knowledge
pack for NotebookLM ingestion:

  - ``output/notebooklm_source.md`` -- the original consolidated export
    (macro/portfolio/follows), plus a trailing note pointing at the 5
    modular files below.
  - ``output/notebooklm/01_macro_and_regime.md`` -- macro & regime detail.
  - ``output/notebooklm/02_portfolio_and_greeks.md`` -- portfolio & options
    Greeks detail.
  - ``output/notebooklm/03_strategy_signals_and_picks.md`` -- strategy
    signals & picks.
  - ``output/notebooklm/04_trade_journal_and_ledger.md`` -- the trade
    journal / ledger.
  - ``output/notebooklm/05_options_directives_and_matrix.md`` -- options
    directives & the pricing matrix.

Each of the 6 output files is generated and written INDEPENDENTLY -- see
``build_export()``'s docstring for the critical crash-isolation fix this
enforces.
"""

import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import pandas as pd

# Repo-root import shim
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Venv re-exec + .env loading
from scripts._bootstrap import bootstrap  # noqa: E402
bootstrap()

from data.historical_store import HistoricalStore  # noqa: E402
from pilots.follows_store import FollowsStore  # noqa: E402
from pilots.portfolio import serialize_portfolio  # noqa: E402
from settings import settings  # noqa: E402

logger = logging.getLogger("notebooklm_export")


# ---------------------------------------------------------------------------
# The 5 modular per-domain generators are defined further down in this same
# module. `build_export()` looks each one up BY NAME (via `globals()`) at
# call time rather than calling it directly, purely so that a `NameError`
# from a not-yet-defined name is caught by the SAME per-section try/except
# every other failure mode goes through -- there is exactly one failure path
# to reason about, not two. The 5 names/signatures are:
#
#   generate_macro_regime_source(store, out_dir: Path) -> str
#   generate_portfolio_greeks_source(store, out_dir: Path) -> str
#   generate_signals_picks_source(out_dir: Path) -> str
#   generate_trade_journal_source(out_dir: Path) -> str
#   generate_options_matrix_source(out_dir: Path) -> str
#
# A test exercising the modular path monkeypatches these names onto this
# module the same way the existing test suite already monkeypatches
# `HistoricalStore`/`FollowsStore`.
# ---------------------------------------------------------------------------

_MODULAR_SECTION_FILENAMES: Tuple[str, ...] = (
    "01_macro_and_regime.md",
    "02_portfolio_and_greeks.md",
    "03_strategy_signals_and_picks.md",
    "04_trade_journal_and_ledger.md",
    "05_options_directives_and_matrix.md",
)

# (section key, output filename, human title used in the section's honest
# fallback file, argument arity -- "store" sections receive (store, out_dir);
# "plain" sections receive (out_dir) alone).
_SECTION_SPECS: Tuple[Tuple[str, str, str, str], ...] = (
    ("macro", "01_macro_and_regime.md", "Macro & Regime Context", "store"),
    ("portfolio", "02_portfolio_and_greeks.md", "Portfolio & Options Greeks", "store"),
    ("signals", "03_strategy_signals_and_picks.md", "Strategy Signals & Picks", "plain"),
    ("trades", "04_trade_journal_and_ledger.md", "Trade Journal & Ledger", "plain"),
    ("options", "05_options_directives_and_matrix.md", "Options Directives & Pricing Matrix", "plain"),
)

# Valid `--section` CLI choices / `section=` kwarg values, derived from the
# spec table above so the two can never drift apart.
_SECTION_CHOICES: Tuple[str, ...] = tuple(spec[0] for spec in _SECTION_SPECS)


def _section_compute_fn(key: str, arity: str, store, out_dir: Path) -> Callable[[], str]:
    """Returns a zero-arg callable that dispatches to the real modular
    generator function for ``key``, looked up BY NAME from this module's own
    global namespace at call time (see the module-level comment above for
    why that indirection exists and is deliberate, not an oversight).
    """
    generator_name = {
        "macro": "generate_macro_regime_source",
        "portfolio": "generate_portfolio_greeks_source",
        "signals": "generate_signals_picks_source",
        "trades": "generate_trade_journal_source",
        "options": "generate_options_matrix_source",
    }[key]

    def _call() -> str:
        generator = globals()[generator_name]  # raises NameError if not defined
        if arity == "store":
            return generator(store, out_dir)
        return generator(out_dir)

    return _call


def _iter_section_specs(store, modular_dir: Path) -> Iterator[Tuple[str, str, str, Callable[[], str]]]:
    """Yields ``(key, filename, title, compute_fn)`` for each of the 5
    modular sections, with ``compute_fn`` already bound to the correct
    generator + arguments for that section.
    """
    for key, filename, title, arity in _SECTION_SPECS:
        yield key, filename, title, _section_compute_fn(key, arity, store, modular_dir)


class _OneShotMacroDataEngine:
    """Adapter passed to ``HistoricalStore.get_macro(..., data_engine=...)``
    so the three independent VIX/T10Y2Y/HY-OAS lookups below share ONE live
    FRED fetch instead of each independently re-triggering
    ``fetch_macro_history()``.

    ``store`` below is constructed ``readonly=True`` (SQLite ``mode=ro``), so
    ``get_macro()``'s own cache-freshness top-up WRITE always fails and is
    silently swallowed -- meaning its staleness check never actually clears
    and every one of the three ``get_macro()`` calls would otherwise
    independently re-fetch ALL FRED series from the network, every single
    run. This wrapper caps that at exactly one live fetch per script
    invocation instead of up to three.
    """

    def __init__(self) -> None:
        self._df = None
        self._fetched = False

    def fetch_macro_history(self):
        if not self._fetched:
            self._fetched = True
            try:
                if settings.FRED_API_KEY:
                    from data_engine import DataEngine
                    self._df = DataEngine(settings.FRED_API_KEY).fetch_macro_history()
            except Exception as exc:
                logger.warning(f"NotebookLM export: one-shot macro fetch failed: {exc}")
            if self._df is None:
                self._df = pd.DataFrame()
        return self._df


# ---------------------------------------------------------------------------
# Shared formatting helpers -- every one of the 5 modular generators below
# funnels through THESE, never a locally-redefined copy, so a fix to one of
# them (e.g. the pandas.NA/NaT handling in `_is_missing`) automatically
# covers every section instead of needing to be re-applied 5 times.
# ---------------------------------------------------------------------------

def _is_missing(value: Any) -> bool:
    """Robust "is this missing" check shared by every ``_fmt_*`` helper
    below.

    Covers:
      - ``None``.
      - Python ``float('nan')`` and ``numpy.float64('nan')`` -- confirmed
        empirically that ``numpy.float64`` is a genuine subclass of Python
        ``float`` in this repo's pinned numpy version, so the classic
        ``isinstance(value, float) and value != value`` check already
        catches both.
      - ``pandas.NA`` / ``pandas.NaT`` -- neither is an instance of
        ``float``, so a bare ``isinstance`` check silently lets them
        through, and a naive formatter would then ``str()``-format them as
        literal ``"<NA>"``/``"NaT"`` garbage instead of degrading to
        ``"N/A"``.

    ``pd.isna(value)`` is used for the general case, guarded so it can never
    raise: a genuinely unexpected input type is treated as "not missing"
    rather than crashing a formatting helper, and an array-like input (which
    makes ``pd.isna`` return an array instead of a scalar bool) is likewise
    treated as "not missing" -- this function is only ever meant to answer
    the question for a single scalar value.
    """
    if value is None:
        return True
    if isinstance(value, float) and value != value:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if isinstance(result, bool):
        return result
    # A non-scalar input (e.g. a Series/ndarray passed in error) makes
    # pd.isna() return an array rather than a bool -- never crash here;
    # the caller passed something this function was never meant to receive.
    return False


def _fmt_money(value: Any) -> str:
    """Money formatter: ``None``/NaN/``pd.NA``/``pd.NaT`` -> ``"N/A"``; a
    genuine ``0``/``0.0`` renders as an honest ``"$0.00"`` -- CONSTRAINT #4,
    missing data and a real zero balance must never be conflated.

    Deliberately does NOT catch a formatting error on a present-but-corrupt
    value (e.g. a hand-edited/legacy DB row whose numeric column holds a
    string) -- it raises, same as the pre-refactor version. Every call site
    in this module sits inside a section-level "buffer-then-commit"
    try/except specifically so that ONE corrupt item degrades its WHOLE
    section honestly (see ``tests/test_export_notebooklm.py``'s
    ``TestPartialAppendProtection``) rather than silently rendering "N/A"
    for just that one field alongside otherwise-good data.
    """
    if _is_missing(value):
        return "N/A"
    return f"${value:,.2f}"


def _fmt_num(value: Any) -> str:
    """Plain string formatter with the same missing-value degradation as
    ``_fmt_money``.
    """
    if _is_missing(value):
        return "N/A"
    return str(value)


def _fmt_pct(value: Any, precision: int = 2) -> str:
    """Percent formatter for fields like win_rate / kelly_target /
    hmm_risk_on / True_IVR: ``None``/NaN/``pd.NA``/``pd.NaT`` -> ``"N/A"``;
    else ``f"{float(value):.{precision}f}%"``. A secondary ``"N/A"``
    fallback covers a genuinely non-numeric value (e.g. a stray string) that
    slips past ``_is_missing`` and fails the ``float()`` coercion -- this
    function must never raise.
    """
    if _is_missing(value):
        return "N/A"
    try:
        return f"{float(value):.{precision}f}%"
    except (TypeError, ValueError):
        return "N/A"


def _md_escape(value: Any, default: str = "N/A") -> str:
    """Escape a value for safe embedding in a Markdown table cell / bullet
    text.

    ``None`` renders as ``default`` -- not silently as an empty cell, which
    could look like a rendering bug rather than an honestly-missing value
    (CONSTRAINT #4); pass ``default=""``/``"Unknown"``/``"Headline"`` at a
    call site where a different fallback reads better. Escapes a literal
    ``|`` (which would otherwise terminate a Markdown table cell early and
    corrupt the row -- the confirmed bug this helper exists to fix: a real
    ``buy_range``/``sell_range`` string like ``"Trim @ $13.30 | Stop @
    $13.07"`` spliced raw into a table cell injects extra pipe-delimited
    columns and shifts every column after it out of alignment) and
    collapses embedded newlines/carriage returns to a single space (which
    would otherwise break the row across multiple lines).
    """
    if value is None:
        return default
    text = str(value)
    text = text.replace("|", "\\|")
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return text


def _load_json_file(path: Path) -> Dict[str, Any]:
    """Safe JSON load for a modular generator that reads a sidecar JSON
    artifact (``state_snapshot.json``, ``options_matrix.json``).

    Returns ``{}`` on a missing file or any parse/read error. A warning is
    logged only for a genuine parse/read error -- simple absence is an
    expected, honest "this feature hasn't produced output yet" state, not a
    bug, and must not spam the log. Never raises.
    """
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning(f"NotebookLM export: failed to parse JSON file {path}: {exc}")
        return {}
    if not isinstance(data, dict):
        logger.warning(
            f"NotebookLM export: JSON file {path} did not contain an object "
            "at the top level -- ignoring"
        )
        return {}
    return data


def _atomic_write_file(path: Path, content: str) -> None:
    """Generalizes this script's original PID+TID-scoped atomic-write idiom
    to ALL 6 output files (the consolidated export plus the 5 modular
    section files), not just the one file the pre-refactor script wrote.

    Creates parent directories as needed. The temp filename is scoped by
    both the writing process's pid AND the writing thread's identity --
    a bare ``path.with_suffix(".tmp")`` is NOT race-safe, since two
    concurrent invocations targeting the same output path could collide on
    the same temp name.

    On any write/rename failure: logs a warning, cleans up the stray temp
    file (if it was created), and RE-RAISES -- callers that want a section
    to degrade independently rather than propagate the failure (i.e.
    everything in ``build_export()`` below) are responsible for catching
    this at their own call site.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
    except Exception as exc:
        logger.warning(f"Failed to write export file to {path}: {exc}")
        tmp_path.unlink(missing_ok=True)
        raise
    logger.info(f"Export file successfully written to {path}")


# ---------------------------------------------------------------------------
# Generator 1: Macro & Regime (01_macro_and_regime.md)
# ---------------------------------------------------------------------------

def generate_macro_regime_source(store, output_dir: Path) -> str:
    """Generate the Macro & Regime source document (01_macro_and_regime.md).

    Parameters
    ----------
    store:
        An already-constructed ``HistoricalStore(readonly=True)`` instance,
        or ``None`` if construction failed upstream (degrades the macro
        series section to an honest "unavailable" message).
    output_dir:
        Directory containing ``state_snapshot.json`` (typically
        ``settings.OUTPUT_DIR``, passed explicitly by the caller rather than
        read from ``settings`` directly here, so a ``--section`` single-file
        run can redirect it).

    Never raises (CONSTRAINT #6) -- every risky block below is individually
    wrapped in try/except so a ``store=None``, an empty macro series, a
    missing/corrupt ``state_snapshot.json``, or a non-numeric
    ``hmm_risk_on_probability`` each degrade to honest placeholder text
    instead of propagating past this function's own boundary.
    """
    lines: List[str] = []
    lines.append("# Market Regime & Macroeconomic Risk Assessment")
    lines.append(f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    try:
        output_dir = Path(output_dir)
    except Exception:
        output_dir = Path(".")

    # 1. Executive Summary & Regime Classification
    lines.append("## Executive Summary & Regime Classification")
    try:
        snapshot = _load_json_file(output_dir / "state_snapshot.json")

        market_regime = snapshot.get("market_regime") or "UNKNOWN"

        hmm_regime_state_raw = snapshot.get("hmm_regime_state")
        hmm_regime_state = (
            str(hmm_regime_state_raw) if hmm_regime_state_raw not in (None, "") else "N/A"
        )

        # HMM risk-on probability is stored as a raw 0-1 fraction; rendered
        # here as a percent. A genuine 0.0 (0%) still renders honestly as
        # "0.0%", distinct from "didn't run".
        hmm_risk_on_raw = snapshot.get("hmm_risk_on_probability")
        hmm_risk_on_str = "N/A"
        if hmm_risk_on_raw is not None:
            try:
                hmm_risk_on_str = f"{float(hmm_risk_on_raw) * 100:.1f}%"
            except (TypeError, ValueError):
                hmm_risk_on_str = "N/A"

        macro_kill_switch_raw = snapshot.get("macro_kill_switch")
        macro_kill_switch_str = (
            "TRIGGERED (Risk Off)" if macro_kill_switch_raw else "Normal (Inactive)"
        )

        # settings.MACRO_REGIME_GATE_ENABLED semantics: absence/anything but
        # an explicit False means "enabled" (default-True gate).
        macro_gate_raw = snapshot.get("macro_regime_gate_enabled")
        macro_gate_str = "Enabled" if macro_gate_raw is not False else "Disabled"

        try:
            kill_switch_sentinel = (output_dir / "KILL_SWITCH").exists()
        except Exception:
            kill_switch_sentinel = False
        kill_switch_str = "ACTIVE (Trading Halted)" if kill_switch_sentinel else "Clear"

        lines.append(f"- **Market Regime**: {market_regime}")
        lines.append(f"- **HMM Regime State**: {hmm_regime_state}")
        lines.append(f"- **HMM Risk-On Probability**: {hmm_risk_on_str}")
        lines.append(f"- **Macro Kill Switch**: {macro_kill_switch_str}")
        lines.append(f"- **Macro Gate Protection**: {macro_gate_str}")
        lines.append(f"- **Global Kill Switch Sentinel**: {kill_switch_str}")
    except Exception as exc:
        logger.warning(f"NotebookLM macro export: failed to render regime summary: {exc}")
        lines.append("- **Market Regime**: UNKNOWN")
        lines.append("- **HMM Regime State**: N/A")
        lines.append("- **HMM Risk-On Probability**: N/A")
        lines.append("- **Macro Kill Switch**: Normal (Inactive)")
        lines.append("- **Macro Gate Protection**: Enabled")
        lines.append("- **Global Kill Switch Sentinel**: Clear")
    lines.append("")

    # 2. Core Macroeconomic Indicators
    lines.append("## Core Macroeconomic Indicators")
    try:
        if store is None:
            raise RuntimeError("HistoricalStore unavailable")
        macro_engine = _OneShotMacroDataEngine()
        vix_series = store.get_macro("VIXCLS", data_engine=macro_engine)
        t10y2y_series = store.get_macro("T10Y2Y", data_engine=macro_engine)
        hy_oas_series = store.get_macro("BAMLH0A0HYM2", data_engine=macro_engine)

        has_macro = False
        if vix_series is not None and not vix_series.empty:
            lines.append(f"- **VIX**: {_fmt_num(vix_series.iloc[-1])}")
            has_macro = True
        if t10y2y_series is not None and not t10y2y_series.empty:
            lines.append(f"- **10Y-2Y Spread**: {_fmt_num(t10y2y_series.iloc[-1])}%")
            has_macro = True
        if hy_oas_series is not None and not hy_oas_series.empty:
            lines.append(f"- **High Yield OAS**: {_fmt_num(hy_oas_series.iloc[-1])}%")
            has_macro = True

        if not has_macro:
            lines.append("Macro data is currently unavailable.")
    except Exception as exc:
        logger.warning(f"NotebookLM macro export: failed to fetch macro data: {exc}")
        lines.append("Macro data is currently unavailable.")
    lines.append("")

    # 3. Tactical Implications -- static, informative grounding text.
    lines.append("## Tactical Implications for NotebookLM Analysis")
    lines.append(
        "- VIX > 30 or High Yield OAS > 6% gates options premium-selling "
        "strategies closed (VRP regime rules fail closed; see "
        "`technical_options_engine.py`'s premium-sell gate)."
    )
    lines.append(
        "- A 10Y-2Y yield curve inversion is a classic late-cycle "
        "recession signal; a sustained inversion warrants a defensive "
        "posture across the book."
    )
    lines.append(
        "- An HMM risk-on probability below 30% down-weights signal "
        "contributions via the regime multiplier (see "
        "`sizing/position_sizer.py`'s ordered pipeline)."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generator 2: Portfolio & Greeks (02_portfolio_and_greeks.md)
# ---------------------------------------------------------------------------

def generate_portfolio_greeks_source(store, output_dir: Path) -> str:
    """Generate the Portfolio Holdings & Net Risk Greeks source document
    (02_portfolio_and_greeks.md).

    ``store`` is an already-constructed ``HistoricalStore(readonly=True)``,
    or ``None`` if construction failed upstream. ``output_dir`` is accepted
    for signature parity with the other modular-knowledge-pack generators.

    Three independent sections, each with its own try/except so a failure
    in one can never blank another:

    1. Account Liquidity & Capital Summary -- reuses the existing
       ``store.latest_account_snapshot()`` -> ``serialize_portfolio()`` path.
    2. Net Portfolio Greeks & Beta Sensitivity -- calls
       ``pilots.paper_broker.get_portfolio_greeks()`` (lazy import, NO
       arguments passed to it -- it resolves its own store + SPY quote
       internally). This is the fix for a CONFIRMED bug: a now-abandoned
       prior attempt at this feature called
       ``pilots.options_risk.calculate_portfolio_greeks()`` directly with
       zero arguments, which silently takes the "no positions" branch
       (``positions`` stays ``None``) and returns an all-zero-looking result
       with no exception and no indication anything is wrong, even against
       a real, sizeable account (CONSTRAINT #4 violation). The correct
       wiring already lives in ``pilots.paper_broker.get_portfolio_greeks``,
       so it is called directly rather than reinvented here.
    3. Open Positions & Basis -- a markdown table over the *same* portfolio
       payload used in section 1 (not re-fetched), so it only renders when
       section 1 actually succeeded.
    """
    lines: List[str] = []
    lines.append("# Portfolio Holdings, Allocation & Net Risk Greeks")
    lines.append(f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    # ------------------------------------------------------------------
    # 1. Account Liquidity & Capital Summary
    # ------------------------------------------------------------------
    lines.append("## Account Liquidity & Capital Summary")
    port: Optional[Dict[str, Any]] = None
    try:
        if store is None:
            raise RuntimeError("HistoricalStore unavailable")
        snap = store.latest_account_snapshot()
        if snap is None:
            raise RuntimeError("No account snapshot available")
        # Buffer-then-commit: a snapshot that fails partway through
        # formatting must never leave earlier real lines in the document
        # immediately followed by the except branch's fallback message.
        section_lines = []
        port_local = serialize_portfolio(snap)
        section_lines.append(f"- **Total Equity**: {_fmt_money(port_local.get('total_equity'))}")
        section_lines.append(f"- **Buying Power**: {_fmt_money(port_local.get('buying_power'))}")
        fetched_at = port_local.get("fetched_at")
        if fetched_at:
            staleness = " (stale)" if port_local.get("is_stale") else ""
            section_lines.append(f"- **Snapshot As Of**: {fetched_at}{staleness}")
        source = port_local.get("source")
        if source:
            section_lines.append(f"- **Source**: {source}")
        lines.extend(section_lines)
        # Only commit `port` (used by section 3 below) once the whole
        # section rendered successfully.
        port = port_local
    except Exception as exc:
        logger.warning(f"Failed to fetch portfolio snapshot for Greeks export: {exc}")
        lines.append("Portfolio snapshot is unavailable.")
        port = None
    lines.append("")

    # ------------------------------------------------------------------
    # 2. Net Portfolio Greeks & Beta Sensitivity
    # ------------------------------------------------------------------
    lines.append("## Net Portfolio Greeks & Beta Sensitivity")
    lines.append(
        "_Greeks are computed over the platform's paper-trading engine "
        "positions (`PaperAccountStore`), which is a separate book from "
        "the live brokerage account summarized above -- the two may hold "
        "different positions._"
    )
    try:
        # Lazy import matching this repo's convention for optional/heavy
        # dependencies. Deliberately calling the ALREADY-CORRECT wiring
        # instead of `pilots.options_risk.calculate_portfolio_greeks()`
        # directly -- see the docstring above for why that call would
        # silently fabricate an all-zero-looking result (CONSTRAINT #4).
        from pilots.paper_broker import get_portfolio_greeks
        greeks = get_portfolio_greeks()

        section_lines = []
        section_lines.append(f"- **Net Delta (Shares)**: {_fmt_num(greeks.get('net_delta_shares'))}")
        section_lines.append(f"- **Net Dollar Delta ($)**: {_fmt_money(greeks.get('net_dollar_delta'))}")
        section_lines.append(f"- **Net Gamma**: {_fmt_num(greeks.get('net_gamma'))}")
        section_lines.append(f"- **Net Daily Theta ($/day)**: {_fmt_money(greeks.get('net_theta_daily'))}")
        section_lines.append(f"- **Net Vega (1% IV Shock)**: {_fmt_money(greeks.get('net_vega_1pct'))}")
        section_lines.append(f"- **Beta-Weighted SPY Delta**: {_fmt_num(greeks.get('beta_weighted_delta_spy'))}")
        spy_spot = greeks.get("spy_spot")
        if spy_spot is not None:
            section_lines.append(f"- **Benchmark SPY Spot**: {_fmt_money(spy_spot)}")
        missing = greeks.get("positions_with_missing_data") or []
        if missing:
            section_lines.append(
                f"- **Positions with Missing Greeks Data**: {', '.join(str(m) for m in missing)}"
            )
        estimated_beta = greeks.get("symbols_with_estimated_beta") or []
        if estimated_beta:
            section_lines.append(
                f"- **Symbols Using Estimated Beta**: {', '.join(str(s) for s in estimated_beta)}"
            )
        lines.extend(section_lines)
    except Exception as exc:
        logger.warning(f"Failed to compute portfolio Greeks for NotebookLM export: {exc}")
        lines.append("Portfolio Greeks calculation is currently unavailable.")
    lines.append("")

    # ------------------------------------------------------------------
    # 3. Open Positions & Basis
    # ------------------------------------------------------------------
    lines.append("## Open Positions & Basis")
    try:
        if port is None:
            raise RuntimeError("Portfolio snapshot unavailable")
        positions = port.get("positions", [])
        if positions:
            section_lines = [
                "| Symbol | Quantity | Avg Cost | Price | Market Value | Unrealized P&L |",
                "|---|---|---|---|---|---|",
            ]
            for p in positions:
                symbol = _md_escape(p.get("symbol") or "Unknown")
                qty = _fmt_num(p.get("qty"))
                avg_cost = _fmt_money(p.get("avg_cost"))
                price = _fmt_money(p.get("current_price"))
                mkt_val = _fmt_money(p.get("market_value"))
                upl = _fmt_money(p.get("unrealized_pl"))
                section_lines.append(f"| {symbol} | {qty} | {avg_cost} | {price} | {mkt_val} | {upl} |")
            lines.extend(section_lines)
        else:
            lines.append("No open positions.")
    except Exception as exc:
        logger.warning(f"Failed to render open positions table for NotebookLM export: {exc}")
        lines.append("Position details unavailable.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generator 3: Strategy Signals & Picks (03_strategy_signals_and_picks.md)
# ---------------------------------------------------------------------------

def _first_present(d: Dict[str, Any], primary: str, fallback: str) -> Any:
    """Return ``d[primary]`` if present and not ``None``, else ``d.get(fallback)``.

    Used for the documented fallback pairs in the ``signals`` list (e.g.
    ``advisory_action`` falling back to ``action``, ``advisory_conviction``
    falling back to ``conviction``, ``xsec_12_1m`` falling back to
    ``xsec_momentum_rank``) -- a *missing*/``None`` primary key falls back; a
    genuine falsy-but-present value (``0.0``, ``""``, ``False``) is NOT
    treated as missing (CONSTRAINT #4 -- never fabricate/discard a real
    value).
    """
    val = d.get(primary)
    if val is not None:
        return val
    return d.get(fallback)


def _fmt_signal_num(value: Any, decimals: int = 4) -> str:
    """Format a numeric signal field, or ``"N/A"`` for missing/NaN/
    non-numeric -- never fabricates a value, never raises."""
    if _is_missing(value):
        return "N/A"
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_kelly_pct(kelly_target: Any) -> str:
    """``kelly_target`` is a 0-1 FRACTION -- multiply by 100 for percent
    display. ``"N/A"`` when the key is absent/None/NaN/non-numeric; a
    genuine ``kelly_target`` of ``0.0`` renders honestly as ``"0.00%"``,
    never silently coerced to N/A (CONSTRAINT #4)."""
    if _is_missing(kelly_target):
        return "N/A"
    try:
        return f"{float(kelly_target) * 100:.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_bool_honest(value: Any) -> str:
    """Render a genuine bool honestly (``True``/``False`` -> ``"Yes"``/``"No"``);
    ``"N/A"`` only when the field itself is missing (``None``) -- a real
    ``False`` must render as ``"No"``, never be conflated with "unknown"
    (CONSTRAINT #4)."""
    if value is None:
        return "N/A"
    return "Yes" if bool(value) else "No"


def generate_signals_picks_source(output_dir: Path) -> str:
    """Generate the Strategy Signals, Tactical Execution & Pilot Follows
    source document (03_strategy_signals_and_picks.md).

    ``output_dir`` is the directory containing ``state_snapshot.json`` (i.e.
    ``settings.OUTPUT_DIR``) -- passed explicitly by the caller, never read
    from ``settings`` directly here, so this function is independently
    testable against an isolated tmp directory.

    Never raises past this function's boundary (CONSTRAINT #6): every
    section degrades to an honest "unavailable"/"N/A" message on any failure
    rather than propagating, and a failure in one section (e.g. the Follows
    store) never prevents the others (state_snapshot-derived sections) from
    rendering, and vice versa.
    """
    lines: List[str] = []
    lines.append("# Quantitative Strategy Signals, Tactical Execution & Pilot Follows")
    lines.append(f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    # ------------------------------------------------------------------
    # 1. Active Pilot Strategy Subscriptions
    # ------------------------------------------------------------------
    lines.append("## Active Pilot Strategy Subscriptions")
    try:
        follows = FollowsStore(path=str(Path(output_dir) / "follows.json")).list_active()
        if follows:
            lines.append("| Pilot ID | Allocated Amount | Status |")
            lines.append("|---|---|---|")
            for f in follows:
                pilot_id = _md_escape(f.get("pilot_id", "Unknown"))
                amount_str = _fmt_money(f.get("amount"))
                status = _md_escape(f.get("status", "Unknown"))
                lines.append(f"| {pilot_id} | {amount_str} | {status} |")
        else:
            lines.append("No active pilot follows.")
    except Exception as exc:
        logger.warning(f"Failed to fetch active pilot follows: {exc}")
        lines.append("Active pilot follows are unavailable.")
    lines.append("")

    # ------------------------------------------------------------------
    # Load state_snapshot.json ONCE, shared by all three signal-derived
    # sections below -- a load failure degrades every one of them
    # identically and independently of the Follows section above.
    # ------------------------------------------------------------------
    snapshot = _load_json_file(Path(output_dir) / "state_snapshot.json")
    raw_signals = snapshot.get("signals") if snapshot else None
    signals: Optional[List[Dict[str, Any]]] = (
        raw_signals if isinstance(raw_signals, list) and raw_signals else None
    )

    # ------------------------------------------------------------------
    # 2. Daily Tactical Recommendations (BUY / SELL / HOLD)
    # ------------------------------------------------------------------
    lines.append("## Daily Tactical Recommendations (BUY / SELL / HOLD)")
    if signals is None:
        lines.append("Tactical recommendations are currently unavailable.")
    else:
        lines.append("| Symbol | Action | Conviction | Buy Range | Sell Range | Kelly Sizing | Final Score |")
        lines.append("|---|---|---|---|---|---|---|")
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            symbol = _md_escape(sig.get("symbol", "Unknown"))
            action = _md_escape(_first_present(sig, "advisory_action", "action") or "Unknown")
            conviction = _fmt_signal_num(_first_present(sig, "advisory_conviction", "conviction"), decimals=2)
            # THE FIX: buy_range/sell_range are free-text and MAY themselves
            # contain a literal " | " (e.g. "Trim @ $13.30 | Stop @ $13.07")
            # -- MUST go through _md_escape before interpolation, or they
            # inject extra pipe-delimited cells and corrupt this row (and
            # every column after it) against the 7-column header above.
            buy_range = _md_escape(sig.get("buy_range"), default="N/A")
            sell_range = _md_escape(sig.get("sell_range"), default="N/A")
            kelly = _fmt_kelly_pct(sig.get("kelly_target"))
            score = _fmt_signal_num(sig.get("score"), decimals=2)
            lines.append(
                f"| {symbol} | {action} | {conviction} | {buy_range} | {sell_range} | {kelly} | {score} |"
            )
    lines.append("")

    # ------------------------------------------------------------------
    # 3. Multifactor Z-Score Attribution -- only rendered when the signals
    #    table above rendered (same `signals is None` gate).
    # ------------------------------------------------------------------
    lines.append("## Multifactor Z-Score Attribution")
    if signals is None:
        lines.append("Multifactor Z-score attribution is currently unavailable.")
    else:
        lines.append("| Symbol | Value Z | Quality Z | Momentum (XSec) | LowVol Z | Size Z | Composite |")
        lines.append("|---|---|---|---|---|---|---|")
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            symbol = _md_escape(sig.get("symbol", "Unknown"))
            value_z = _fmt_signal_num(sig.get("value_z"))
            quality_z = _fmt_signal_num(sig.get("quality_z"))
            momentum_xsec = _fmt_signal_num(_first_present(sig, "xsec_12_1m", "xsec_momentum_rank"))
            lowvol_z = _fmt_signal_num(sig.get("lowvol_z"))
            size_z = _fmt_signal_num(sig.get("size_z"))
            composite = _fmt_signal_num(sig.get("multifactor_composite"))
            lines.append(
                f"| {symbol} | {value_z} | {quality_z} | {momentum_xsec} | {lowvol_z} | {size_z} | {composite} |"
            )
    lines.append("")

    # ------------------------------------------------------------------
    # 4. Sizing Guardrails & ETF Transmission Impact -- same gate again.
    #    `etf_transmission_multiplier` is ORCHESTRATOR-ONLY (written by
    #    main_orchestrator.py's separate _write_state_snapshot() path) --
    #    absent on the advisory-path snapshot is expected/correct and
    #    renders "N/A" honestly, not a bug.
    # ------------------------------------------------------------------
    lines.append("## Sizing Guardrails & ETF Transmission Impact")
    if signals is None:
        lines.append("Sizing guardrail telemetry is currently unavailable.")
    else:
        lines.append("| Symbol | Was Capped | Binding Constraint | ETF Transmission Multiplier |")
        lines.append("|---|---|---|---|")
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            symbol = _md_escape(sig.get("symbol", "Unknown"))
            was_capped = _fmt_bool_honest(sig.get("sizing_was_capped"))
            binding_constraint_raw = sig.get("sizing_binding_constraint")
            binding_constraint = _md_escape(
                binding_constraint_raw if binding_constraint_raw is not None else "None"
            )
            etf_mult = _fmt_signal_num(sig.get("etf_transmission_multiplier"))
            lines.append(f"| {symbol} | {was_capped} | {binding_constraint} | {etf_mult} |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generator 4: Trade Journal & Ledger (04_trade_journal_and_ledger.md)
# ---------------------------------------------------------------------------

def generate_trade_journal_source(output_dir: Path) -> str:
    """Generate the Quantitative Trade Journal & Realized Performance source
    document (04_trade_journal_and_ledger.md).

    ``output_dir`` is accepted for interface consistency with the other
    generator functions in this module; this generator does not read local
    files directly, only ``pilots.trade_history.trade_history_view``.

    Renders THREE distinct states based on the ``available``/``n_trades``
    signal ``trade_history_view`` already computes -- fixing a confirmed bug
    where "zero closed trades ever" and "the trade-history fetch/ingest
    failed" both rendered the identical message:

    (a) ``available=False`` -> ingest-has-not-run-yet message (NOT
        necessarily zero real trades).
    (b) ``available=True`` and ``n_trades == 0`` -> genuine zero-trades
        message (the durable store IS populated; this account just hasn't
        closed any positions).
    (c) ``n_trades > 0`` -> full KPI + ledger table.

    Never raises past this function's boundary (CONSTRAINT #6) -- a
    ``trade_history_view`` failure degrades to an honest "unavailable"
    message, distinct from both (a) and (b).
    """
    lines: List[str] = []
    lines.append("# Quantitative Trade Journal & Realized Performance")
    lines.append(f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    lines.append("## Realized Trading KPIs (FIFO Reconstructed)")
    th_view: Optional[Dict[str, Any]] = None
    try:
        from pilots.trade_history import trade_history_view

        th_view = trade_history_view(limit=50, offset=0)
    except Exception as exc:  # noqa: BLE001 - never raise past this generator
        logger.warning(f"Failed to fetch trade history view: {exc}")
        lines.append(
            "Trade history is currently unavailable (an error occurred while "
            "querying the durable broker-fills store)."
        )
        th_view = None

    if th_view is not None:
        summary = th_view.get("summary") or {}
        n_trades = int(summary.get("n_trades") or 0)
        available = bool(th_view.get("available"))

        if not available:
            # (a) The durable store has zero persisted fills ever -- an
            # ingest/data-availability gap, NOT necessarily proof the account
            # has no real closed trades. Distinct from (b) below -- THIS IS
            # THE BUG FIX: previously both (a) and (b) rendered the identical
            # "No realized closed trade history recorded yet." message.
            lines.append(
                "Trade history ingest has not run yet, or the durable store "
                "is empty -- this is not necessarily zero real trades."
            )
        elif n_trades == 0:
            # (b) The store IS populated (ingest has run at least once) but
            # genuinely reconstructed zero closed round-trips for this
            # account/filter. Distinct from (a) above.
            lines.append(
                "No realized closed trades yet -- this account has not "
                "closed any positions."
            )
        else:
            # (c) Real trades exist -- render the full KPI block. A genuine
            # 0.0 (e.g. exactly break-even total P&L over real trades) still
            # renders honestly here -- it is never conflated with "no data"
            # because we only reach this branch when n_trades > 0.
            win_rate = summary.get("win_rate")
            win_rate_pct = None if _is_missing(win_rate) else float(win_rate) * 100
            profit_factor = summary.get("profit_factor")
            profit_factor_str = "N/A" if _is_missing(profit_factor) else f"{float(profit_factor):.2f}"
            avg_holding_days = summary.get("avg_holding_days")
            avg_holding_str = (
                "N/A" if _is_missing(avg_holding_days) else f"{float(avg_holding_days):.1f} days"
            )

            lines.append(f"- **Total Closed Trades**: {n_trades}")
            lines.append(f"- **Win Rate**: {_fmt_pct(win_rate_pct)}")
            lines.append(f"- **Profit Factor**: {profit_factor_str}")
            lines.append(f"- **Total Realized P&L**: {_fmt_money(summary.get('total_realized_pnl'))}")
            lines.append(
                f"- **Gross Profit | Gross Loss**: {_fmt_money(summary.get('gross_profit'))} | "
                f"{_fmt_money(summary.get('gross_loss'))}"
            )
            lines.append(
                f"- **Average Win | Average Loss**: {_fmt_money(summary.get('avg_win'))} | "
                f"{_fmt_money(summary.get('avg_loss'))}"
            )
            # avg_return_pct is ALREADY a percent value (e.g. 5.23 for 5.23%)
            # per data/robinhood_orders.py's own inline comment -- do NOT
            # multiply by 100 again here (that was part of the confirmed bug
            # class this generator must avoid reintroducing).
            lines.append(f"- **Average Return per Trade**: {_fmt_pct(summary.get('avg_return_pct'))}")
            lines.append(f"- **Average Holding Duration**: {avg_holding_str}")
            lines.append(
                f"- **Best Trade | Worst Trade**: {_fmt_money(summary.get('best_trade_pnl'))} | "
                f"{_fmt_money(summary.get('worst_trade_pnl'))}"
            )
    lines.append("")

    lines.append("## Recent Closed Trades Ledger")
    trades = (th_view or {}).get("trades") or []
    if trades:
        lines.append(
            "| Symbol | Quantity | Entry Date | Exit Date | Holding Days | "
            "Entry Price | Exit Price | Realized P&L | Return % |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for t in trades:
            symbol = _md_escape(t.get("symbol"), default="N/A")
            quantity = _fmt_num(t.get("quantity"))
            entry_ts = t.get("entry_ts")
            entry_date = entry_ts[:10] if isinstance(entry_ts, str) and entry_ts else "N/A"
            exit_ts = t.get("exit_ts")
            exit_date = exit_ts[:10] if isinstance(exit_ts, str) and exit_ts else "N/A"
            holding_days_val = t.get("holding_days")
            holding_days = (
                "N/A" if _is_missing(holding_days_val) else _fmt_num(round(float(holding_days_val), 1))
            )
            entry_price = _fmt_money(t.get("entry_price"))
            exit_price = _fmt_money(t.get("exit_price"))
            realized_pnl = _fmt_money(t.get("realized_pnl"))
            return_pct = _fmt_pct(t.get("return_pct"))
            lines.append(
                f"| {symbol} | {quantity} | {entry_date} | {exit_date} | {holding_days} | "
                f"{entry_price} | {exit_price} | {realized_pnl} | {return_pct} |"
            )
    else:
        # Honest empty-ledger message. If `available=False` or `n_trades==0`
        # was already reported above, this second honest-empty line is
        # expected/fine, not suppressed.
        lines.append("No closed trades available.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generator 5: Options Directives & Matrix (05_options_directives_and_matrix.md)
# ---------------------------------------------------------------------------

def _is_finite_number(value: Any) -> bool:
    """True for a real (non-NaN, non-bool) int/float. Guards against bool
    (a subclass of int in Python) and NaN (value != value) sneaking into a
    numeric-formatting branch."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return value == value  # False for NaN


def _resolve_ivr(directive: Dict[str, Any]) -> Optional[float]:
    """THE BUG FIX: ``dict.get(key, default)``'s ``default`` only fires when
    the key is ABSENT, never when it's present with value ``None`` --
    ``technical_options_engine.py::build_premium_directive`` always
    initializes BOTH ``"IVR_Proxy"`` and ``"True_IVR"`` in every directive
    row (so the key is always present, just often ``null``/NaN when the
    chain-derived True_IVR couldn't be computed). The old, buggy pattern --
    ``d.get("True_IVR", d.get("IVR_Proxy"))`` -- therefore NEVER falls back
    to IVR_Proxy in production, silently showing "N/A" for a column that
    frequently has a real, available value. This explicitly checks for
    None/NaN instead.
    """
    ivr_value = directive.get("True_IVR")
    if ivr_value is None or (isinstance(ivr_value, float) and ivr_value != ivr_value):
        ivr_value = directive.get("IVR_Proxy")
    if _is_finite_number(ivr_value):
        return float(ivr_value)
    return None


def generate_options_matrix_source(output_dir: Path) -> str:
    """Generate the Options Directives & Volatility Matrix source document
    (05_options_directives_and_matrix.md).

    Reads ``options_matrix.json`` from ``output_dir`` (the file written by
    ``reporting/options_snapshot.py::write_options_matrix`` -- top-level
    shape ``{timestamp, target_dte, vix, market_regime, directives: [...]}``).
    Never raises (CONSTRAINT #6); a missing/unreadable/malformed file, or one
    whose ``"directives"`` key is missing or not a list, degrades every
    section to its honest "unavailable"/N/A/UNKNOWN/0 rendering rather than
    crashing the whole export. Never fabricates a value (CONSTRAINT #4) -- a
    genuinely null/NaN field always renders "N/A"; a real 0 renders as "0".
    """
    lines: List[str] = []
    lines.append("# Options Strategy Directives & Volatility Matrix")
    lines.append(f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    data = _load_json_file(Path(output_dir) / "options_matrix.json")

    directives_raw = data.get("directives") if isinstance(data, dict) else None
    directives: List[Dict[str, Any]] = directives_raw if isinstance(directives_raw, list) else []

    # ── Options Environment & Regime Gating ─────────────────────────────
    lines.append("## Options Environment & Regime Gating")
    target_dte = data.get("target_dte") if isinstance(data, dict) else None
    target_dte_str = "N/A" if target_dte is None else f"{target_dte} days"
    vix = data.get("vix") if isinstance(data, dict) else None
    market_regime = (data.get("market_regime") if isinstance(data, dict) else None) or "UNKNOWN"
    lines.append(f"- **Target DTE**: {target_dte_str}")
    lines.append(f"- **Reference VIX**: {_fmt_num(vix)}")
    lines.append(f"- **Market Regime**: {market_regime}")
    lines.append(f"- **Directives Generated**: {len(directives)}")
    lines.append("")

    # ── Active Quantitative Directives (Credit Spreads & Condors) ───────
    lines.append("## Active Quantitative Directives (Credit Spreads & Condors)")
    if directives:
        lines.append(
            "| Symbol | Strategy | Action | Spot Price | Short Leg | Long Leg "
            "| Net Premium | IV Rank | Trend Bias |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for d in directives:
            if not isinstance(d, dict):
                continue
            symbol = _md_escape(d.get("Symbol"))
            strategy = _md_escape(d.get("Strategy"))
            action = _md_escape(d.get("Action"))
            price = _fmt_money(d.get("Price"))

            short_strike = d.get("Short_Strike")
            if short_strike is not None:
                short_leg = f"{short_strike} (Δ {d.get('Short_Delta')})"
            else:
                short_leg = "N/A"

            long_strike = d.get("Long_Strike")
            if long_strike is not None:
                long_leg = f"{long_strike} (Δ {d.get('Long_Delta')})"
            else:
                long_leg = "N/A"

            net_premium = _fmt_money(d.get("Net_Premium"))

            # THE FIX in action -- see _resolve_ivr's docstring above.
            ivr_value = _resolve_ivr(d)
            ivr_str = "N/A" if ivr_value is None else f"{ivr_value:.2f}%"

            trend_bias = _md_escape(d.get("Trend_Bias"))

            lines.append(
                f"| {symbol} | {strategy} | {action} | {price} | {short_leg} "
                f"| {long_leg} | {net_premium} | {ivr_str} | {trend_bias} |"
            )
    else:
        lines.append("No active options directives available.")
    lines.append("")

    # ── Candidate Fundamental Health & News Catalysts ───────────────────
    lines.append("## Candidate Fundamental Health & News Catalysts")
    if directives:
        for d in directives[:10]:
            if not isinstance(d, dict):
                continue
            symbol = _md_escape(d.get("Symbol"))
            lines.append(f"### {symbol}")
            lines.append(f"- **Altman Z-Score**: {_fmt_num(d.get('Altman_Z_Score'))}")
            lines.append(f"- **Piotroski F-Score**: {_fmt_num(d.get('Piotroski_F_Score'))}")
            lines.append(f"- **Days To Earnings**: {_fmt_num(d.get('Days_To_Earnings'))}")
            lines.append(f"- **Earnings Risk**: {'Yes' if d.get('Earnings_Risk') else 'No'}")

            news = d.get("News_Snippets")
            if isinstance(news, list) and news:
                lines.append("- **News Catalysts**:")
                for item in news[:3]:
                    if isinstance(item, dict):
                        # NOT item.get("title", "Headline") -- that has the
                        # exact same "default only fires on an ABSENT key"
                        # flaw as _resolve_ivr's bug above. _md_escape's own
                        # default= parameter is None-aware, so it correctly
                        # falls back to "Headline" whether the key is
                        # absent or present-but-None.
                        title = _md_escape(item.get("title"), default="Headline")
                    else:
                        title = _md_escape(item, default="Headline")
                    lines.append(f"  - {title}")
            lines.append("")
    else:
        lines.append("No candidate fundamental/news data available.")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Consolidated (single-file) source -- preserves the original build_export()
# behavior byte-for-behavior-identical, plus one new trailing section.
# ---------------------------------------------------------------------------

def generate_consolidated_source(store, output_dir: Path) -> str:
    """Renders the consolidated Markdown export as a string.

    This is the original single-file ``build_export()`` logic (Macro
    Context / Current Portfolio / Active Pilot Follows), extracted
    VERBATIM except that it now returns the rendered Markdown instead of
    writing it directly -- the actual atomic write is the caller's
    responsibility (see ``build_export()``). Behavior for every existing
    scenario in ``tests/test_export_notebooklm.py`` is unchanged; the one
    addition is a new trailing ``## Modular Sources Note`` section listing
    the 5 modular per-domain files this refactor introduces.

    ``store`` is a (possibly ``None``, on construction failure) pre-built
    ``HistoricalStore`` shared across sections -- constructed once by
    ``build_export()``, not here, so a construction failure degrades every
    store-dependent section identically without this function needing to
    know why.
    """
    lines: List[str] = []
    lines.append("# Stockpy System Export")
    lines.append(f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    # 1. Macro Context
    lines.append("## Macro Context")
    try:
        if store is None:
            raise RuntimeError("HistoricalStore unavailable")
        macro_engine = _OneShotMacroDataEngine()
        vix_series = store.get_macro("VIXCLS", data_engine=macro_engine)
        t10y2y_series = store.get_macro("T10Y2Y", data_engine=macro_engine)
        hy_oas_series = store.get_macro("BAMLH0A0HYM2", data_engine=macro_engine)

        has_macro = False
        if not vix_series.empty:
            lines.append(f"- **VIX**: {_fmt_num(vix_series.iloc[-1])}")
            has_macro = True
        if not t10y2y_series.empty:
            lines.append(f"- **10Y-2Y Spread**: {_fmt_num(t10y2y_series.iloc[-1])}%")
            has_macro = True
        if not hy_oas_series.empty:
            lines.append(f"- **High Yield OAS**: {_fmt_num(hy_oas_series.iloc[-1])}%")
            has_macro = True

        if not has_macro:
            lines.append("Macro data is currently unavailable.")
    except Exception as exc:
        logger.warning(f"Failed to fetch macro data: {exc}")
        lines.append("Macro data is currently unavailable.")
    lines.append("")

    # 2. Portfolio
    lines.append("## Current Portfolio")
    try:
        if store is None:
            raise RuntimeError("HistoricalStore unavailable")
        snap = store.latest_account_snapshot()
        if snap:
            # Built into a local buffer and only merged into `lines` once the
            # WHOLE section completes without raising — a later position
            # that fails to format (e.g. a hand-edited/legacy DB row) must
            # never leave earlier real lines in the document immediately
            # followed by the except branch's "unavailable" message below.
            section_lines = []
            port = serialize_portfolio(snap)
            section_lines.append(f"- **Total Equity**: {_fmt_money(port.get('total_equity'))}")
            section_lines.append(f"- **Buying Power**: {_fmt_money(port.get('buying_power'))}")
            fetched_at = port.get("fetched_at")
            if fetched_at:
                staleness = " (stale)" if port.get("is_stale") else ""
                section_lines.append(f"- **Snapshot As Of**: {fetched_at}{staleness}")
            section_lines.append("")
            positions = port.get("positions", [])
            if positions:
                section_lines.append("### Positions")
                for p in positions:
                    symbol = p.get('symbol', 'Unknown')
                    qty = _fmt_num(p.get('qty'))
                    avg_cost = _fmt_money(p.get('avg_cost'))
                    mkt_val = _fmt_money(p.get('market_value'))
                    name = p.get('name') or ''
                    name_str = f" ({name})" if name else ""
                    section_lines.append(f"- **{symbol}**{name_str}: {qty} shares @ {avg_cost} (Market Value: {mkt_val})")
            else:
                section_lines.append("No open positions.")
            lines.extend(section_lines)
        else:
            lines.append("Portfolio snapshot is unavailable.")
    except Exception as exc:
        logger.warning(f"Failed to fetch portfolio: {exc}")
        lines.append("Portfolio snapshot is unavailable.")
    lines.append("")

    # 3. Active Follows
    lines.append("## Active Pilot Follows")
    try:
        follows = FollowsStore().list_active()
        if follows:
            # Same buffer-then-commit discipline as the Portfolio section
            # above: a later follow row that fails to format must not leave
            # earlier real follow lines in the document.
            section_lines = []
            for f in follows:
                pilot_id = f.get('pilot_id', 'Unknown')
                amount = _fmt_money(f.get('amount'))
                status = f.get('status', 'Unknown')
                section_lines.append(f"- **Pilot ID**: {pilot_id} | **Amount**: {amount} | **Status**: {status}")
            lines.extend(section_lines)
        else:
            lines.append("No active pilot follows.")
    except Exception as exc:
        logger.warning(f"Failed to fetch active follows: {exc}")
        lines.append("Active pilot follows are unavailable.")

    # 4. NEW: Modular Sources Note -- the only behavioral addition vs. the
    # pre-refactor single-file export.
    lines.append("")
    lines.append("## Modular Sources Note")
    lines.append(
        "This consolidated file summarizes core account/macro/follows "
        "state. For deeper per-domain detail (regime diagnostics, options "
        "Greeks, strategy signals, the trade ledger, and the options "
        f"pricing matrix), see the modular files under `{output_dir / 'notebooklm'}`:"
    )
    lines.append("")
    for fname in _MODULAR_SECTION_FILENAMES:
        lines.append(f"- `notebooklm/{fname}`")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def build_export(
    output_dir: Optional[Path] = None,
    *,
    modular: bool = True,
    consolidated: bool = True,
    section: Optional[str] = None,
) -> None:
    """Generates the NotebookLM knowledge pack: the consolidated
    ``notebooklm_source.md`` and/or the 5 modular ``notebooklm/0N_*.md``
    files, per ``modular``/``consolidated``/``section``.

    THE CRITICAL FIX this function embodies: a now-abandoned prior attempt
    at this feature called each of the 5 modular generators from this
    driver with NO try/except of its own -- only each generator's OWN
    internal logic had scoped try/excepts around specific known-risky
    external calls. Live-reproduced failure: a malformed value deep inside
    one generator's per-row formatting loop (e.g. a non-numeric
    ``kelly_target`` making ``float(s["kelly_target"])`` raise
    ``ValueError``) propagated all the way out of this driver and aborted
    the ENTIRE script with exit code 1 -- every section scheduled AFTER the
    failing one (e.g. trade-journal, options-matrix files) was never
    written at all, even though nothing was wrong with them. This directly
    contradicts the intended "each of the 5 sections degrades
    independently" design.

    THE FIX, applied uniformly to all 5 modular files: each file's
    compute-and-write pair is wrapped in its OWN try/except here in the
    driver. On any exception, a warning is logged naming the failing
    section, and an HONEST fallback markdown file is written for THAT file
    only -- never nothing, and never a stale leftover from a previous run,
    since an operator seeing a totally-missing file cannot tell "this
    section crashed" from "this feature was never enabled". Every sibling
    section still gets generated and written normally regardless of any
    other section's failure.
    """
    out_dir = output_dir or settings.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Constructed once, up front, and shared by every store-dependent
    # section below — NOT re-created per section. A construction failure
    # here degrades every store-dependent section to "unavailable" (store
    # stays None), but each section still runs its own try/except so a
    # failure fetching one kind of data can never take down a sibling
    # section that doesn't depend on it.
    #
    # Note: `readonly=True` is DB-write-enforced (SQLite `mode=ro`), but it
    # does NOT prevent `get_macro()`'s internal staleness top-up from making
    # a live FRED network call before its write attempt fails closed — see
    # `HistoricalStore.get_macro()`'s own docstring. `_OneShotMacroDataEngine`
    # caps that at one live fetch per run instead of one per series.
    try:
        store = HistoricalStore(readonly=True)
    except Exception as exc:
        logger.warning(f"Failed to construct HistoricalStore: {exc}")
        store = None

    # --- Consolidated export -------------------------------------------------
    # NOTE: deliberately NOT wrapped in its own try/except here, unlike the 5
    # modular sections below. `generate_consolidated_source()` already has
    # its own internal per-subsection try/excepts (macro/portfolio/follows),
    # so in practice this only ever raises on a genuine I/O failure inside
    # `_atomic_write_file` -- and that failure is meant to propagate out of
    # `build_export()` exactly as it did pre-refactor (see
    # `tests/test_export_notebooklm.py::TestAtomicWrite`, which asserts
    # `build_export()` itself raises `OSError` on a write failure). The
    # CONFIRMED CRITICAL BUG this module fixes was specifically about the 5
    # *modular* generator calls having no try/except of their own -- the
    # consolidated path's write semantics were already correct and must stay
    # byte-for-behavior-identical.
    if consolidated and section is None:
        consolidated_path = out_dir / "notebooklm_source.md"
        content = generate_consolidated_source(store, out_dir)
        _atomic_write_file(consolidated_path, content)
        print(f"Export written to {consolidated_path}")

    # --- Modular per-domain sources ------------------------------------------
    if modular or section is not None:
        modular_dir = out_dir / "notebooklm"
        modular_dir.mkdir(parents=True, exist_ok=True)

        # NOTE: generators are handed `out_dir` (where `state_snapshot.json`
        # / `options_matrix.json` actually live -- the same directory the
        # store-based macro/portfolio generators read from), NOT
        # `modular_dir` (which is only where THIS function writes the
        # rendered .md files, via `target` below). Conflating the two here
        # would make every JSON-file-reading generator (signals/trades/
        # options) silently read from the wrong directory and always
        # degrade to "unavailable", even when real upstream data exists.
        for key, filename, title, compute_fn in _iter_section_specs(store, out_dir):
            if section not in (None, key):
                continue
            target = modular_dir / filename
            try:
                content = compute_fn()
                _atomic_write_file(target, content)
            except Exception as exc:
                logger.warning(
                    f"NotebookLM export: section '{key}' failed to generate: {exc}"
                )
                fallback = f"# {title}\n\n_This source failed to generate this run: {exc}_\n"
                try:
                    _atomic_write_file(target, fallback)
                except Exception as write_exc:
                    logger.warning(
                        f"NotebookLM export: failed to write fallback for "
                        f"section '{key}': {write_exc}"
                    )
            else:
                print(f"Export written to {target}")


def main() -> None:
    # This script's CLI flags below are NOT dead scaffolding -- do not remove
    # the `parser.parse_args()` call as a cleanup.
    #
    # `scripts/build_command_manifest.py` (via `cli_introspect/capture.py`)
    # introspects every entry point in `cli_introspect/targets.py` -- this
    # script included -- by monkeypatching `ArgumentParser.parse_args` to
    # capture the built parser and unwind BEFORE any real work runs. That
    # harness needs `parse_args()` to actually be called, unconditionally, at
    # the top of `__main__`, or it falls through, `build_export()` runs for
    # real (a live DB read + real file writes), and the target is
    # dead-lettered out of the manifest with "parse_args was never called" --
    # exactly what happened when this scaffolding was previously removed as
    # "dead" in PR #971. It's also what makes `--help` side-effect-free for a
    # human operator, instead of silently running the real export.
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate a NotebookLM modular knowledge-pack export."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override the output directory (defaults to settings.OUTPUT_DIR).",
    )
    parser.add_argument(
        "--modular-only",
        action="store_true",
        help="Only generate the 5 modular notebooklm/0N_*.md files; skip the consolidated export.",
    )
    parser.add_argument(
        "--consolidated-only",
        action="store_true",
        help="Only generate the consolidated notebooklm_source.md; skip the 5 modular files.",
    )
    parser.add_argument(
        "--section",
        choices=_SECTION_CHOICES,
        default=None,
        help="Generate only this one modular section (implies --modular-only for that section).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    build_export(
        output_dir=args.output_dir,
        modular=not args.consolidated_only,
        consolidated=not args.modular_only,
        section=args.section,
    )


if __name__ == "__main__":
    main()
