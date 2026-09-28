"""Implied-volatility engine: extracts ATM option IVs, performs calendar-30-day linear interpolation, and computes lookahead-free true IV rank and the Volatility Risk Premium (VRP) used to gate premium-selling strategies."""

import os
import logging
import math
from datetime import datetime, date, timedelta
from typing import Optional, Any, Tuple, List, Dict, Iterable
import pandas as pd
import numpy as np
from sqlalchemy import Column, Integer, String, Float, UniqueConstraint, text, inspect
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import resolve_database_url, create_db_engine, session_scope

logger = logging.getLogger("IV_Engine")

# Database configuration consistent with transactions_store.py
DB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(DB_DIR, "quant_platform.db")
DATABASE_URL = f"sqlite:///{DB_FILE}"

# ─────────────────────────────────────────────────────────────────────────────
# Provenance tags for ``iv_history.source`` (added 2026-09; see
# docs/known_issues/bootstrap_iv_history_provenance_fabrication_risk.md).
#
# CONSTRAINT #4: ``calculate_true_ivr()`` ranks a genuine, live,
# options-chain-derived ``current_iv`` reading against this table's history
# via ``IVHistoryStore.get_historical_ivs()``. Before this tag existed, a
# realized-vol-derived PROXY value written by ``volatility/bootstrap_iv_
# history.py`` (``rolling_vol + 0.038`` -- NOT a real implied volatility) was
# indistinguishable from a genuine chain-derived row, so a live premium-
# selling trade gate (``True_IVR > 50``, see CLAUDE.md's VRP-gate convention)
# could silently rank against fabricated-looking data with no way for a
# caller or operator to tell. ``get_historical_ivs()`` now excludes
# ``IV_SOURCE_SYNTHETIC_BOOTSTRAP`` rows by default (CONSTRAINT #6 -- fail
# closed rather than rank against a value this table cannot vouch for).
# ─────────────────────────────────────────────────────────────────────────────
IV_SOURCE_CHAIN = "chain"
"""Genuine, options-chain-derived IV -- written by the live pipeline
(``pipeline/production_steps.py::OptionsAnalysisStep``,
``technical_options_engine.py``'s opt-in real-IVR path) via
``get_30d_atm_iv()``. The ``record_iv()`` default -- every existing
production caller relies on this default rather than passing it explicitly."""

IV_SOURCE_SYNTHETIC_BOOTSTRAP = "synthetic_bootstrap"
"""A realized-volatility-derived PROXY (NOT a real implied volatility) --
written only by ``volatility/bootstrap_iv_history.py``, which passes this
explicitly. Excluded by default from ``get_historical_ivs()``'s ranking
history so a real ``current_iv`` reading is never ranked against a
fabricated-looking proxy without disclosure."""

IV_SOURCE_LEGACY_UNKNOWN = "legacy_unknown"
"""Backfilled onto pre-existing rows by the additive ``source`` column
migration (``_migrate_add_source_column``) for a database that predates
provenance tracking. Genuinely unknown, not asserted real -- CONSTRAINT #4
forbids retroactively labeling a legacy row ``IV_SOURCE_CHAIN`` when this
codebase cannot verify it. Deliberately NOT excluded from ranking by
default: every ``record_iv()`` caller that existed before this migration
was a genuine chain-derived write (only ``bootstrap_iv_history.py`` could
have written a synthetic row, and only going forward from when it starts
passing ``IV_SOURCE_SYNTHETIC_BOOTSTRAP`` explicitly), so treating legacy
rows as excludable-if-in-doubt would silently blank out real history on
every pre-existing installation for no compensating safety benefit."""

Base = declarative_base()

class IVHistory(Base):
    """
    ORM Model for storing historical 30-day ATM implied volatilities.
    """
    __tablename__ = 'iv_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False)
    date = Column(String(10), nullable=False)  # Format: YYYY-MM-DD
    iv_30d_atm = Column(Float, nullable=False)
    # Provenance tag -- see the IV_SOURCE_* constants above. Required (no
    # column-level default): every write path threads an explicit value
    # through record_iv()'s own `source` parameter (which DOES default to
    # IV_SOURCE_CHAIN), so a fresh CREATE TABLE never needs to backfill rows.
    source = Column(String(30), nullable=False)

    __table_args__ = (
        UniqueConstraint('ticker', 'date', name='_ticker_date_uc'),
    )

class IVHistoryStore:
    def __init__(self, db_url: Optional[str] = None):
        db_url = db_url or resolve_database_url()
        self.engine = create_db_engine(db_url)
        Base.metadata.create_all(self.engine)
        self._migrate_add_source_column()
        self.Session = sessionmaker(bind=self.engine)

    def _migrate_add_source_column(self) -> None:
        """Additive migration: add ``iv_history.source`` to a pre-existing
        database that predates provenance tracking (the bootstrap-IV-history
        fabrication-risk fix -- see the module-level comment above the
        IV_SOURCE_* constants).

        Idempotent -- probes the table's real columns first so a fresh DB
        (whose ``CREATE TABLE`` above already includes ``source``, or one
        already migrated) never attempts a duplicate ``ALTER TABLE``.

        Existing rows are backfilled with ``IV_SOURCE_LEGACY_UNKNOWN`` rather
        than a fabricated ``IV_SOURCE_CHAIN`` value (CONSTRAINT #4) -- this
        codebase cannot know, after the fact, whether a pre-migration row
        came from the live options-chain-derived pipeline or from a manual
        ``bootstrap_iv_history.py`` run. ``get_historical_ivs()`` only
        excludes rows explicitly tagged ``IV_SOURCE_SYNTHETIC_BOOTSTRAP``, so
        legacy rows keep ranking exactly as before this fix -- this
        migration closes the FORWARD-LOOKING contamination risk, it does
        not (and cannot) retroactively re-attribute historical rows.

        Uses ``sqlalchemy.inspect()`` rather than a raw ``PRAGMA
        table_info`` probe: ``IVHistoryStore`` is a dual-backend
        (SQLite/Postgres) store via ``db_config.create_db_engine()``, and
        ``PRAGMA`` is SQLite-only -- the same dialect-safety reasoning as
        ``data/paper_account_store.py``'s ``_migrate_paper_positions_schema``.

        Never raises (CONSTRAINT #6): a failed migration just means
        ``source`` stays unavailable on this DB, so the pre-fix behavior
        (rank against everything, real or synthetic) persists rather than
        crashing store construction.
        """
        try:
            insp = inspect(self.engine)
            if not insp.has_table("iv_history"):
                return  # Fresh DB: create_all() above already wrote `source`.
            existing_cols = {c["name"] for c in insp.get_columns("iv_history")}
            if "source" in existing_cols:
                return
            with self.engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE iv_history ADD COLUMN source VARCHAR(30) "
                    f"NOT NULL DEFAULT '{IV_SOURCE_LEGACY_UNKNOWN}'"
                ))
            logger.info("IVHistoryStore: migrated iv_history — added source column.")
        except Exception as exc:
            logger.warning(
                "IVHistoryStore._migrate_add_source_column failed (non-fatal): %s", exc
            )

    def record_iv(
        self, ticker: str, date_val: Any, iv_val: float, *, source: str = IV_SOURCE_CHAIN
    ) -> None:
        """
        Inserts or updates an IV record for a specific ticker and date.

        ``source`` defaults to ``IV_SOURCE_CHAIN`` -- every existing
        production caller (``pipeline/production_steps.py::
        OptionsAnalysisStep``, ``technical_options_engine.py``'s real-IVR
        path) relies on this default and never passes ``source`` itself.
        ``volatility/bootstrap_iv_history.py`` is the one caller that MUST
        override this explicitly to ``IV_SOURCE_SYNTHETIC_BOOTSTRAP`` --
        never silently defaulting a synthetic write to look like a real one
        (CONSTRAINT #4).
        """
        try:
            with session_scope(self.Session) as session:
                # Parse date to standard string format
                date_str = _parse_date_to_str(date_val)
                ticker_clean = ticker.upper().strip()

                # Check if record already exists
                record = session.query(IVHistory).filter(
                    IVHistory.ticker == ticker_clean,
                    IVHistory.date == date_str
                ).first()

                if record:
                    record.iv_30d_atm = float(iv_val)
                    record.source = source
                else:
                    record = IVHistory(
                        ticker=ticker_clean,
                        date=date_str,
                        iv_30d_atm=float(iv_val),
                        source=source,
                    )
                    session.add(record)
        except Exception as e:
            logger.error(f"Failed to record IV for {ticker} on {date_val}: {e}")
            raise e

    def get_historical_ivs(
        self,
        ticker: str,
        as_of_date: Any,
        lookback_days: int = 252,
        *,
        exclude_sources: Optional[Iterable[str]] = (IV_SOURCE_SYNTHETIC_BOOTSTRAP,),
    ) -> List[float]:
        """
        Fetches historical IV values prior to the given as_of_date (strict no-lookahead).

        ``exclude_sources`` defaults to excluding ``IV_SOURCE_SYNTHETIC_
        BOOTSTRAP`` rows -- ``volatility/bootstrap_iv_history.py``'s
        realized-vol-derived proxy values, which are NOT genuine
        options-chain-derived IV and must never be ranked against a real
        current IV reading as if they were (CONSTRAINT #4). This is the
        enforcement point ``calculate_true_ivr()`` (``pilots/volatility_
        surface.py``) relies on -- it calls this method with no override,
        so it inherits the exclusion automatically. Pass an empty
        tuple/``None`` to disable filtering (e.g. a diagnostic/audit tool
        that deliberately wants the raw, unfiltered history).
        """
        session = self.Session()
        try:
            date_str = _parse_date_to_str(as_of_date)
            ticker_clean = ticker.upper().strip()

            query = session.query(IVHistory).filter(
                IVHistory.ticker == ticker_clean,
                IVHistory.date < date_str
            )
            if exclude_sources:
                query = query.filter(~IVHistory.source.in_(list(exclude_sources)))
            records = query.order_by(IVHistory.date.desc()).limit(lookback_days).all()

            return [r.iv_30d_atm for r in records]
        except Exception as e:
            logger.error(f"Failed to retrieve IV history for {ticker} prior to {as_of_date}: {e}")
            return []
        finally:
            session.close()


def _parse_date_to_str(d: Any) -> str:
    """Helper to parse datetime, date, or string into standard YYYY-MM-DD string."""
    if isinstance(d, str):
        return d.strip()[:10]
    elif isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    elif isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    else:
        raise ValueError(f"Unsupported date format: {d}")


def get_30d_atm_iv(data_engine: Any, ticker: str, as_of_date: Any, spot_price: Optional[float] = None) -> float:
    """
    Fetches the front-month and second-month option chains and linear interpolates to 30 calendar days.
    Averages call and put IV for the closest strike to spot (ATM).
    Returns float(NaN) if any step fails or data is insufficient.
    """
    try:
        as_of_dt = datetime.strptime(_parse_date_to_str(as_of_date), "%Y-%m-%d")
        
        # Get spot price if not provided
        if spot_price is None:
            tech = data_engine.fetch_technical_raw([ticker])
            if ticker in tech and not tech[ticker].empty:
                df_filtered = tech[ticker].loc[tech[ticker].index <= pd.to_datetime(as_of_dt)]
                if not df_filtered.empty:
                    spot_price = float(df_filtered['Close'].iloc[-1])
                    
        if spot_price is None or spot_price <= 0:
            logger.warning(f"No valid spot price for {ticker} as of {as_of_date}. Cannot compute IV.")
            return float('nan')

        # Get all expirations
        expirations = data_engine.fetch_options_chain(ticker)
        if not expirations or len(expirations) == 0:
            logger.warning(f"No expirations returned for {ticker} as of {as_of_date}.")
            return float('nan')

        # Filter and sort expirations strictly in the future relative to as_of_date
        future_exps = []
        for exp in expirations:
            try:
                exp_dt = datetime.strptime(exp, "%Y-%m-%d")
                days_diff = (exp_dt - as_of_dt).days
                if days_diff >= 0:
                    future_exps.append((exp, days_diff))
            except Exception:
                continue

        future_exps.sort(key=lambda x: x[1])

        if len(future_exps) < 2:
            logger.warning(f"Fewer than 2 future expirations found for {ticker} as of {as_of_date}. Exps: {future_exps}")
            # If we only have 1, we can return it as fallback or NaN. Let's return NaN to enforce linear interpolation.
            return float('nan')

        # near term (front-month) and next term (second-month)
        t1, d1 = future_exps[0]
        t2, d2 = future_exps[1]

        # Fetch chains
        chain_1 = data_engine.fetch_options_chain(ticker, t1)
        chain_2 = data_engine.fetch_options_chain(ticker, t2)

        if not chain_1 or not chain_2:
            logger.warning(f"Could not fetch options chains for {ticker} expirations {t1} or {t2}.")
            return float('nan')

        # Compute ATM IV for each chain
        iv1 = _calculate_atm_iv_from_chain(chain_1, spot_price)
        iv2 = _calculate_atm_iv_from_chain(chain_2, spot_price)

        if math.isnan(iv1) or math.isnan(iv2):
            logger.warning(f"Failed to calculate ATM IV for {ticker} at {t1} or {t2}.")
            return float('nan')

        # Linear interpolation to 30 days
        if d2 == d1:
            return float(max(0.0001, iv1))
        
        iv_30 = iv1 + (iv2 - iv1) * (30.0 - d1) / (d2 - d1)
        return float(max(0.0001, iv_30))

    except Exception as e:
        logger.error(f"Error computing 30d ATM IV for {ticker} as of {as_of_date}: {e}")
        return float('nan')


def _calculate_atm_iv_from_chain(chain: Any, spot_price: float) -> float:
    """Helper to extract ATM call/put averaged IV from an OptionChain-like object."""
    try:
        calls = chain.calls
        puts = chain.puts
        
        if calls.empty and puts.empty:
            return float('nan')

        # Find closest strike
        all_strikes = pd.concat([calls['strike'], puts['strike']]).unique()
        if len(all_strikes) == 0:
            return float('nan')
            
        atm_strike = min(all_strikes, key=lambda x: abs(x - spot_price))

        call_iv = float('nan')
        put_iv = float('nan')

        if not calls.empty:
            call_row = calls[calls['strike'] == atm_strike]
            if not call_row.empty and 'impliedVolatility' in call_row.columns:
                call_iv = float(call_row['impliedVolatility'].iloc[0])

        if not puts.empty:
            put_row = puts[puts['strike'] == atm_strike]
            if not put_row.empty and 'impliedVolatility' in put_row.columns:
                put_iv = float(put_row['impliedVolatility'].iloc[0])

        # Average ATM call and put IV
        ivs = [iv for iv in [call_iv, put_iv] if not math.isnan(iv) and iv > 0]
        if len(ivs) > 0:
            return sum(ivs) / len(ivs)
        return float('nan')

    except Exception as e:
        logger.warning(f"Error calculating ATM IV from chain: {e}")
        return float('nan')


from pilots.volatility_surface import calculate_true_ivr as _calc_ivr, get_vrp as _get_vrp

def calculate_true_ivr(ticker: str, current_iv: float, as_of_date: Any, store: IVHistoryStore, lookback_days: int = 252) -> float:
    """Delegates to the canonical ``pilots.volatility_surface`` implementation
    (SSOT). ``store.get_historical_ivs()`` excludes ``IV_SOURCE_SYNTHETIC_
    BOOTSTRAP`` rows by default -- see that method's and ``IVHistoryStore``'s
    docstrings for the full provenance-filtering contract this relies on.
    """
    return _calc_ivr(ticker, current_iv, as_of_date, store, lookback_days)


def get_vrp(ticker: str, current_iv: float, garch_vol: float) -> float:
    """
    Calculates Volatility Risk Premium: implied volatility minus realized forecast volatility.
    Delegates to the canonical pilots.volatility_surface implementation (SSOT).
    """
    return _get_vrp(ticker, current_iv, garch_vol)
