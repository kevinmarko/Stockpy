import time
import asyncio
import pandas as pd
from typing import Dict, List, Any
from .BaseStrategy import BaseStrategy

class GlobalBackfillEngine:
    def __init__(self, registry: Dict[str, BaseStrategy]):
        self.registry = registry

    async def run_full_system_backfill(self, task_id: str, status_callback):
        """
        Executes backfilling across all registered models and emits status events.
        """
        import numpy as np
        from data.market_data import CompositeProvider

        total_strategies = len(self.registry)
        completed = 0

        await status_callback(
            task_id=task_id,
            status="RUNNING",
            progress=0,
            message="Fetching universe data and computing technical features..."
        )

        # 1. Fetch universe data (mocked universe for now, or read from settings.DEFAULT_TICKERS)
        import settings
        tickers = settings.DEFAULT_TICKERS[:10]  # Just use top 10 for speed in backfill if needed, or all.
        
        provider = CompositeProvider()
        price_dict = {}
        volume_dict = {}
        for ticker in tickers:
            try:
                bars = provider.get_intraday_bars(ticker, lookback_days=3000, interval="1d")
                if isinstance(bars, pd.DataFrame) and not bars.empty and "Close" in bars.columns:
                    price_dict[ticker] = bars["Close"].rename(ticker)
                    if "Volume" in bars.columns:
                        volume_dict[ticker] = bars["Volume"].rename(ticker)
            except Exception as e:
                pass

        prices = pd.DataFrame(price_dict).dropna(how="all")
        volumes = pd.DataFrame(volume_dict).dropna(how="all")

        features_list = []
        for ticker in prices.columns:
            if ticker not in volumes.columns: continue
            df = pd.DataFrame({"Close": prices[ticker], "Volume": volumes[ticker]}).dropna()
            if len(df) < 50: continue

            df["Return"] = df["Close"].pct_change()
            df["Vol_20"] = df["Return"].rolling(20).std() * np.sqrt(252)
            df["Vol_50"] = df["Return"].rolling(50).std() * np.sqrt(252)
            
            delta = df["Close"].diff()
            gain = (delta.where(delta > 0, 0.0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
            rs = gain / loss.replace(0.0, np.nan)
            df["RSI_14"] = 100.0 - (100.0 / (1.0 + rs))

            ema_fast = df["Close"].ewm(span=12, adjust=False).mean()
            ema_slow = df["Close"].ewm(span=26, adjust=False).mean()
            df["MACD"] = ema_fast - ema_slow

            vol_ma = df["Volume"].rolling(20).mean()
            df["Vol_Ratio"] = df["Volume"] / vol_ma.replace(0.0, np.nan)
            df["Ticker"] = ticker
            
            # Additional Returns for standard momentum
            df["Return_252d"] = df["Close"].pct_change(periods=252)
            
            features_list.append(df)

        if not features_list:
            raise ValueError("No data fetched.")

        concat_df = pd.concat(features_list).reset_index()
        date_col = "index" if "index" in concat_df.columns else "date" if "date" in concat_df.columns else concat_df.columns[0]
        concat_df.rename(columns={date_col: "Date"}, inplace=True)
        concat_df.set_index(["Date", "Ticker"], inplace=True)
        global_df = concat_df

        results = {}

        for strategy_id, strategy in self.registry.items():
            await status_callback(
                task_id=task_id,
                status="RUNNING",
                progress=int((completed / total_strategies) * 100),
                message=f"Processing model: {strategy.name}..."
            )

            # 1. Fetch historical raw signals using precomputed unified features
            signals_df = strategy.generate_raw_signals(global_df)

            if signals_df is None or signals_df.empty:
                completed += 1
                continue

            # 2. Compute Meta-Labeling triple barriers per horizon
            horizons = strategy.get_supported_horizons()
            model_metrics = []

            for horizon in horizons:
                labeled_df = self._compute_triple_barriers(signals_df, horizon)
                metrics = self._train_purged_meta_labeler(labeled_df, horizon, strategy_id)
                model_metrics.append(metrics)

            results[strategy_id] = model_metrics
            completed += 1

            await status_callback(
                task_id=task_id,
                status="RUNNING",
                progress=int((completed / total_strategies) * 100),
                message=f"Completed {strategy.name} ({completed}/{total_strategies})"
            )

        await status_callback(
            task_id=task_id,
            status="COMPLETED",
            progress=100,
            message="Global backfill & meta-labeling completed successfully!"
        )

        return results

    def _compute_triple_barriers(self, df: pd.DataFrame, horizon_days: int) -> pd.DataFrame:
        # Standardized Triple Barrier outcome generator
        df = df.copy()
        if 'Close' in df.columns:
            df[f'target_{horizon_days}d'] = (df['raw_signal'] * df['Close'].pct_change(horizon_days).shift(-horizon_days) > 0).astype(int)
        return df.dropna()

    def _train_purged_meta_labeler(self, df: pd.DataFrame, horizon: int, strategy_id: str) -> Dict[str, Any]:
        from validation.purged_cv import CombinatorialPurgedCV
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import accuracy_score, roc_auc_score
        import os
        import joblib
        import settings

        features = ["Vol_20", "Vol_50", "RSI_14", "MACD", "Vol_Ratio"]
        target_col = f"target_{horizon}d"
        
        # Ensure we only use rows with all features and target
        model_df = df.dropna(subset=features + [target_col])
        if len(model_df) < 50:
            return {"horizon": horizon, "accuracy": 0.0, "roc_auc": 0.0, "train_n": 0, "test_n": 0}

        X = model_df[features]
        y = model_df[target_col]

        cv = CombinatorialPurgedCV(n_splits=5, n_test_splits=1)
        
        # Calculate OOS metrics using Purged CV
        oos_preds = []
        oos_true = []
        
        clf = RandomForestClassifier(
            n_estimators=settings.FORECAST_BACKFILL_N_ESTIMATORS, 
            max_depth=settings.FORECAST_BACKFILL_MAX_DEPTH, 
            random_state=settings.FORECAST_BACKFILL_RANDOM_STATE
        )

        try:
            for train_idx, test_idx, _ in cv.split(X, y):
                X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
                X_test, y_test = X.iloc[test_idx], y.iloc[test_idx]
                
                clf.fit(X_train, y_train)
                preds = clf.predict(X_test)
                
                oos_preds.extend(preds)
                oos_true.extend(y_test)
        except ValueError as e:
            # Fallback if too few samples for CV
            oos_preds = [0]
            oos_true = [0]

        if len(oos_true) > 0 and len(set(oos_true)) > 1:
            acc = accuracy_score(oos_true, oos_preds)
            try:
                roc_auc = roc_auc_score(oos_true, oos_preds)
            except ValueError:
                roc_auc = 0.5
        else:
            acc = 0.5
            roc_auc = 0.5

        # Final fit on full data
        clf.fit(X, y)

        # Save model
        models_dir = os.path.join(os.path.dirname(__file__), "..", "models")
        os.makedirs(models_dir, exist_ok=True)
        model_path = os.path.join(models_dir, f"meta_{strategy_id}_{horizon}d.pkl")
        joblib.dump(clf, model_path)

        return {
            "horizon": horizon,
            "accuracy": acc,
            "roc_auc": roc_auc,
            "train_n": len(X),
            "test_n": len(oos_true),
        }
