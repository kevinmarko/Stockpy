# ==============================================================================
# MODULE: SYSTEMIC MACROECONOMIC & QUANTITATIVE RESEARCH ENGINE
# File: macro_engine.py
# Description: Implements top-down macro risk assessment ("MACRO FREEZE"),
#              the Fama-French 3-Factor regression model for Alpha isolation,
#              and Google Cloud Natural Language sentiment integration.
# ==============================================================================

import os
import logging
import datetime
from typing import Dict, Any, Optional, List, Tuple
import numpy as np
import pandas as pd
import statsmodels.api as sm
import pandera.pandas as pa
from pandera.typing import Series, DateTime

# Core project imports
from data_engine import DataEngine
from dto_models import MacroEconomicDTO
from regime.hmm_regime import HMMRegimeDetector, build_feature_matrix

# Try importing pandas_datareader for Fama-French factor loading
try:
    import pandas_datareader.data as web  # type: ignore
    DATA_READER_AVAILABLE = True
except ImportError:
    DATA_READER_AVAILABLE = False

# Set up module logger
logger = logging.getLogger("MacroEngine")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# ==============================================================================
# 1. PANDERA SCHEMA FOR MACRO REGIME VALIDATION
# ==============================================================================
class MacroDataSchema(pa.DataFrameModel):
    """
    Validates top-down economic data output from the "Macro Freeze" / "killSwitch" logic.
    Ensures strict type-safety and logical correctness before system ingestion.
    """
    date: Series[DateTime] = pa.Field(nullable=False)
    yield_curve_10y_2y: Series[float] = pa.Field(nullable=False)
    high_yield_oas: Series[float] = pa.Field(ge=0.0, nullable=False)
    sahm_rule_indicator: Series[float] = pa.Field(ge=0.0, nullable=False)
    market_regime: Series[str] = pa.Field(isin=["RISK ON", "NEUTRAL", "RECESSION", "CREDIT EVENT"])

    class Config:
        coerce = True
        strict = True


# ==============================================================================
# 2. MAIN MACRO ENGINE IMPLEMENTATION
# ==============================================================================
class MacroEngine:
    """
    The orchestrator for macroeconomic risk mapping, multi-factor modeling,
    and unstructured news sentiment parsing.
    """

    def __init__(self, data_engine: DataEngine):
        """
        Initializes the MacroEngine with a DataEngine instance for raw FRED data fetching.
        """
        self.data_engine = data_engine
        # Persists across calls within this MacroEngine instance's lifetime so the
        # retrain_freq_days gate (HMMRegimeDetector.fit) is meaningful for a
        # long-lived process. Callers that loop within a single process
        # (main.py's --interval mode / agent loop) MUST reuse ONE MacroEngine
        # instance across cycles -- see main.py's module-level
        # `_get_macro_engine()` singleton -- otherwise a fresh, never-fitted
        # HMMRegimeDetector is constructed every cycle and the retrain gate is
        # meaningless. main_orchestrator.py constructs a fresh MacroEngine per
        # process invocation (one _main_body() call per launch, no internal
        # loop), so that context always "refits" once per launch -- expected
        # for a one-shot script, not a bug (see regime/hmm_regime.py).
        # n_states / retrain_freq_days are operator-tunable via settings so
        # this is not a hardcoded literal (see settings.HMM_N_STATES /
        # settings.HMM_RETRAIN_FREQ_DAYS).
        from settings import settings as _settings
        self._hmm_detector = HMMRegimeDetector(
            n_states=_settings.HMM_N_STATES,
            retrain_freq_days=_settings.HMM_RETRAIN_FREQ_DAYS,
        )

    # Minimum rows required for a numerically stable 3-state Gaussian HMM fit.
    HMM_MIN_FIT_ROWS = 100

    def compute_hmm_risk_on_probability(self, spy_price_df: Optional[pd.DataFrame]) -> Optional[float]:
        """
        Computes the HMM second opinion's risk_on_probability at the latest
        available date, for use as MacroEconomicDTO.hmm_risk_on_probability.

        Returns None (never a fabricated probability) if:
        - spy_price_df is unavailable/empty,
        - DataEngine.fetch_macro_history() returns no usable VIX/yield-curve
          history (e.g. FRED unavailable),
        - the aligned feature matrix has fewer than HMM_MIN_FIT_ROWS rows, or
        - the HMM fit/predict raises (logged, not propagated -- a statistical
          second opinion failing must never crash the primary rules-based
          pipeline).
        """
        if spy_price_df is None or spy_price_df.empty or 'Close' not in spy_price_df.columns:
            logger.warning("HMM regime: no SPY price history available; skipping (hmm_risk_on_probability=None).")
            return None

        # ── Phase 3: route macro history through HistoricalStore ──────────────
        # When HISTORICAL_STORE_ENABLED is True, HistoricalStore.get_macro() tops
        # up only the delta from FRED and serves the rest from quant_platform.db.
        # The single-snapshot _build_macro_dto path (current-state reads) is NOT
        # touched here — only the historical series used by the HMM are cached.
        try:
            from settings import settings as _s
            if _s.HISTORICAL_STORE_ENABLED:
                from data.historical_store import HistoricalStore
                _store = HistoricalStore()
                vix_series = _store.get_macro(
                    "VIXCLS", data_engine=self.data_engine
                )
                t10y2y_series = _store.get_macro(
                    "T10Y2Y", data_engine=self.data_engine
                )
                if vix_series.empty or t10y2y_series.empty:
                    logger.warning(
                        "HMM regime: HistoricalStore returned empty macro series; "
                        "falling back to direct DataEngine fetch."
                    )
                    raise RuntimeError("empty series from HistoricalStore")
                macro_history = pd.DataFrame(
                    {"VIXCLS": vix_series, "T10Y2Y": t10y2y_series}
                )
                logger.debug(
                    "HMM regime: macro history from HistoricalStore "
                    "(%d rows, VIXCLS=%d, T10Y2Y=%d).",
                    len(macro_history), vix_series.notna().sum(), t10y2y_series.notna().sum(),
                )
            else:
                raise RuntimeError("HISTORICAL_STORE_ENABLED=False")
        except Exception as _hist_exc:
            # Graceful fallback: direct DataEngine fetch (pre-Phase-3 behavior).
            logger.debug(
                "HMM regime: HistoricalStore path skipped (%s); "
                "falling back to DataEngine.fetch_macro_history().", _hist_exc,
            )
            try:
                macro_history = self.data_engine.fetch_macro_history() if self.data_engine else pd.DataFrame()
            except Exception as e:
                logger.warning(f"HMM regime: fetch_macro_history() failed: {e}; skipping.")
                return None

        if macro_history is None or macro_history.empty or 'VIXCLS' not in macro_history.columns:
            logger.warning("HMM regime: no usable VIX/yield-curve history; skipping (hmm_risk_on_probability=None).")
            return None

        try:
            features = build_feature_matrix(
                spy_price_df, macro_history['VIXCLS'], macro_history['T10Y2Y']
            )
        except Exception as e:
            logger.warning(f"HMM regime: feature matrix construction failed: {e}; skipping.")
            return None

        if len(features) < self.HMM_MIN_FIT_ROWS:
            logger.warning(
                f"HMM regime: only {len(features)} aligned feature rows "
                f"(< {self.HMM_MIN_FIT_ROWS} required); skipping."
            )
            return None

        try:
            self._hmm_detector.fit(features)
            result = self._hmm_detector.predict_proba(features)
            return float(result["risk_on_probability"])
        except Exception as e:
            logger.error(f"HMM regime: fit/predict failed: {e}. Falling back to None (rules-based stays primary).")
            return None

    def calculate_sahm_rule(self, fallback_val: float = 0.0) -> float:
        """
        Fetches historical monthly Unemployment Rate (UNRATE) from FRED and computes
        the Sahm Rule Recession Indicator dynamically.
        Formula: 3-Month Moving Average minus the minimum 3-Month Moving Average 
                 in the prior 12 months.
        """
        # Attempt to fetch directly if initialized
        if not self.data_engine or not getattr(self.data_engine, 'fred', None):
            logger.warning("FRED API is not active. Using default Sahm Rule fallback.")
            return fallback_val

        try:
            # Dual-path verification: First try to fetch the pre-computed FRED Sahm indicator
            try:
                sahm_series = self.data_engine.fred.get_series('SAHMREALTIME', limit=5)
                if sahm_series is not None and not sahm_series.empty:
                    return float(sahm_series.iloc[-1])
            except Exception as e:
                logger.debug(f"Direct SAHMREALTIME fetch omitted: {e}. Computing manually.")

            # Fallback to computing from UNRATE series history
            unrate_series = self.data_engine.fred.get_series('UNRATE')
            if unrate_series is None or unrate_series.empty:
                return fallback_val

            # Sort index just in case of order issues
            unrate_series = unrate_series.sort_index()

            # Calculate 3-month moving average
            ma3 = unrate_series.rolling(window=3).mean()
            # Find the minimum ma3 over the previous 12 months
            min_ma3 = ma3.rolling(window=12).min()

            if ma3.empty or min_ma3.empty:
                return fallback_val

            sahm_indicator = ma3.iloc[-1] - min_ma3.iloc[-1]
            return float(sahm_indicator)

        except Exception as e:
            logger.error(f"Failed to calculate Sahm Rule from FRED: {e}. Using fallback: {fallback_val}")
            return fallback_val

    def run_macro_killswitch(self, macro_raw: Dict[str, Any], sahm_rule_val: float) -> pd.DataFrame:
        """
        Executes the systemic "MACRO FREEZE" / "killSwitch" logic.
        Outputs a pandas DataFrame that conforms to the MacroDataSchema constraints.
        """
        yield_curve = float(macro_raw.get('T10Y2Y', 0.5))
        credit_spread = float(macro_raw.get('BAMLH0A0HYM2', 3.5))
        
        # Determine Market Regime with relaxed thresholds and compound logic
        if (yield_curve < -0.25 and credit_spread > 6.0) or sahm_rule_val >= 0.6:
            regime = "RECESSION"
        elif credit_spread > 6.0:
            regime = "CREDIT EVENT"
        elif credit_spread > 4.5:
            regime = "NEUTRAL"
        else:
            regime = "RISK ON"

        df = pd.DataFrame({
            "date": [datetime.datetime.now()],
            "yield_curve_10y_2y": [yield_curve],
            "high_yield_oas": [credit_spread],
            "sahm_rule_indicator": [sahm_rule_val],
            "market_regime": [regime]
        })

        # Validate DataFrame to ensure it strictly conforms to schema constraints
        return MacroDataSchema.validate(df)

    def fetch_proxy_factors_offline(self, index: pd.Index) -> pd.DataFrame:
        """
        Builds a stable, offline-compatible set of proxy factors when Tuck French 
        or yfinance servers are unreachable. Prevents execution failures.
        """
        logger.info("Generating synthetic Fama-French factors (Offline Fallback)...")
        np.random.seed(42)  # Set seed for reproducible test suites
        n = len(index)

        # Draw factors from stylized historical parameters
        mkt_rf = np.random.normal(0.0003, 0.01, n)
        smb = np.random.normal(0.0001, 0.005, n)
        hml = np.random.normal(0.0001, 0.005, n)
        rf = np.ones(n) * 0.00015  # Approx 3.8% annualized rate

        return pd.DataFrame({
            'Mkt-RF': mkt_rf,
            'SMB': smb,
            'HML': hml,
            'RF': rf
        }, index=index)

    def calculate_fama_french_alpha(
        self,
        stock_returns: pd.Series,
        factors_df: Optional[pd.DataFrame] = None
    ) -> Dict[str, float]:
        """
        Implements the Fama-French 3-Factor regression model.
        Regresses the stock's excess returns against the Market Premium (Mkt-RF), 
        Size (SMB), and Value (HML) factors to extract Alpha (intercept).
        
        Formula: R_i - R_f = alpha + beta1*(R_m - R_f) + beta2*SMB + beta3*HML + error
        """
        # Ensure clean input data
        stock_returns = stock_returns.dropna()
        if len(stock_returns) < 5:
            raise ValueError(f"Insufficient stock return points for regression. Count: {len(stock_returns)}")

        # If factors are not passed, fetch them using pandas_datareader if available
        if factors_df is None:
            if DATA_READER_AVAILABLE:
                try:
                    logger.info("Fetching Fama-French factors via pandas_datareader...")
                    # Fetching 'F-F_Research_Data_Factors' from famafrench
                    ff_dict = web.DataReader(
                        'F-F_Research_Data_Factors', 
                        'famafrench', 
                        start=stock_returns.index.min(), 
                        end=stock_returns.index.max()
                    )
                    # Extract the monthly factor DataFrame (Index 0)
                    df = ff_dict[0]
                    # Convert PeriodIndex to datetime index
                    df.index = df.index.to_timestamp()
                    # Rescale values to decimals (Ken French's data is formatted as percents, e.g. 1.2% = 1.2)
                    df = df / 100.0
                    
                    # Align / resample monthly factors to match the stock returns timeline
                    # Using forward-fill for daily alignment if stock returns are daily
                    factors_df = df.reindex(stock_returns.index, method="ffill")
                except Exception as e:
                    logger.warning(f"Failed to fetch factors via pandas_datareader: {e}. Falling back to proxy factors.")
            
            # Fallback to local proxy / synthetic factors if fetching failed or datareader not available
            if factors_df is None:
                factors_df = self.fetch_proxy_factors_offline(stock_returns.index)

        # Align series to matching index dates
        combined = pd.concat([stock_returns, factors_df], axis=1, join="inner").dropna()
        if len(combined) < 5:
            raise ValueError("Empty overlap between stock returns and Fama-French factor timelines.")

        # Compute excess returns (Stock Return minus Risk-Free Rate)
        y = combined.iloc[:, 0] - combined['RF']
        
        # Prepare Regressors Matrix
        X = combined[['Mkt-RF', 'SMB', 'HML']]
        X = sm.add_constant(X)  # Add intercept for Alpha extraction

        # Execute Multiple Linear Regression (OLS)
        model = sm.OLS(y, X).fit()

        # Extract regression parameters
        alpha = float(model.params['const'])
        beta_mkt = float(model.params['Mkt-RF'])
        beta_smb = float(model.params['SMB'])
        beta_hml = float(model.params['HML'])
        p_alpha = float(model.pvalues['const'])
        r_squared = float(model.rsquared)

        return {
            "alpha": alpha,
            "beta_market": beta_mkt,
            "beta_size": beta_smb,
            "beta_value": beta_hml,
            "p_value_alpha": p_alpha,
            "r_squared": r_squared
        }

    def _fallback_sentiment(self, text: str) -> float:
        """Helper to compute rule-based sentiment density as local fallback."""
        pos_keywords = {"bullish", "growth", "buy", "upbeat", "expansion", "profit", "sustainable", "undervalued", "gain", "strong", "outperform"}
        neg_keywords = {"bearish", "recession", "sell", "downside", "weak", "risk", "distress", "pessimism", "loss", "underperform", "slippage"}
        
        clean_words = text.lower().split()
        if not clean_words:
            return 0.0
            
        pos_matches = sum(1 for w in clean_words if any(pk in w for pk in pos_keywords))
        neg_matches = sum(1 for w in clean_words if any(nk in w for nk in neg_keywords))
        
        match_total = pos_matches + neg_matches
        if match_total == 0:
            return 0.0
            
        return float((pos_matches - neg_matches) / match_total)

    def analyze_sentiment(self, text: str) -> float:
        """
        Integrates Google Cloud Natural Language API to parse a text string and
        return a quantified sentiment score in range [-1.0 (negative) to 1.0 (positive)].
        Explicitly initializes the client using GOOGLE_APPLICATION_CREDENTIALS,
        with a graceful try/except block to bypass if unavailable.
        """
        if not text or not isinstance(text, str):
            return 0.0

        # Verify Google Application Credentials environment variable is set
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        
        # Graceful check for a local credentials file if env is not populated
        if not creds_path:
            if os.path.exists("credentials.json"):
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath("credentials.json")
                creds_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]

        # If credentials are not found, gracefully bypass sentiment scoring
        if not creds_path:
            logger.warning("GOOGLE_APPLICATION_CREDENTIALS not found. Bypassing Google NLP API.")
            return self._fallback_sentiment(text)

        try:
            from google.cloud import language_v1
            # Initialize client explicitly - it loads from GOOGLE_APPLICATION_CREDENTIALS automatically
            client = language_v1.LanguageServiceClient()
            document = language_v1.Document(
                content=text,
                type_=language_v1.Document.Type.PLAIN_TEXT
            )
            response = client.analyze_sentiment(request={"document": document})
            sentiment = response.document_sentiment
            return float(sentiment.score)
        except Exception as e:
            logger.warning(f"Google Cloud NL API sentiment analysis failed or rate limited: {e}. Using fallback.")
            return self._fallback_sentiment(text)

    def fetch_and_compile_macro(self, text_context: Optional[str] = None) -> Dict[str, Any]:
        """
        Pulls raw macro data, calculates indicators, evaluates regimes, 
        measures sentiment, and structures the final output to align with MacroEconomicDTO.
        """
        # Fetch FRED indicators
        macro_raw = self.data_engine.fetch_macro_raw()
        
        # Calculate Sahm Rule
        sahm_val = self.calculate_sahm_rule(fallback_val=0.0)
        
        # Evaluate regime and validate schema
        regime_df = self.run_macro_killswitch(macro_raw, sahm_val)
        validated_regime = regime_df["market_regime"].iloc[0]

        # Analyze sentiment
        sentiment_score = 0.0
        if text_context:
            sentiment_score = self.analyze_sentiment(text_context)

        # Fallbacks for nominal 10Y and inflation rate
        inflation = float(macro_raw.get('CPIAUCSL_YoY', 2.0))
        nominal_10y = float(macro_raw.get('DGS10', 4.0))

        # Structure response dictionary aligning with MacroEconomicDTO init standard
        return {
            "yield_curve_10y_2y": float(macro_raw.get('T10Y2Y', 0.5)),
            "high_yield_oas": float(macro_raw.get('BAMLH0A0HYM2', 3.5)),
            "inflation_rate": inflation,
            "nominal_10y": nominal_10y,
            "sahm_rule_indicator": sahm_val,
            "vix_value": float(macro_raw.get('VIXCLS', 15.0)),
            "date": datetime.datetime.now(),
            "sentiment_score": sentiment_score,
            "market_regime": validated_regime
        }
