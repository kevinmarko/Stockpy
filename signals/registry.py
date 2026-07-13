"""
InvestYo Quant Platform - Signal Registry
=========================================
Manages the registration and execution of pluggable SignalModules.
"""

from typing import Dict
import pandas as pd

from signals.base import SignalModule, SignalContext, SignalOutput


class SignalRegistry:
    """Registry that maintains and executes pluggable signal modules."""
    
    def __init__(self):
        self._modules: Dict[str, SignalModule] = {}

    def register(self, module: SignalModule) -> None:
        """Registers a signal module instance."""
        if not module.name:
            raise ValueError("Signal module must have a non-empty 'name' attribute.")
        self._modules[module.name] = module

    def get(self, name: str) -> SignalModule:
        """Retrieves a registered signal module by name."""
        if name not in self._modules:
            raise KeyError(f"Signal module '{name}' not found in registry.")
        return self._modules[name]

    def get_all(self) -> Dict[str, SignalModule]:
        """Returns a copy of all registered signal modules."""
        return self._modules.copy()

    def run_pre_compute(
        self,
        universe_df: pd.DataFrame,
        context: SignalContext,
    ) -> None:
        """Call each module's pre_compute hook once per orchestrator cycle.

        Iterates the registry and invokes ``pre_compute(universe_df, context)``
        on every module.  Modules that have not overridden the hook (i.e. all
        per-ticker signal modules) execute the inherited no-op and return
        immediately.  Only cross-sectional modules (e.g.
        ``CrossSectionalMomentumSignal``) perform real work here.

        Parameters
        ----------
        universe_df : pd.DataFrame
            Current-cycle dashboard DataFrame with one row per ticker.
            Must contain at least a ``Symbol`` column and any features
            needed by cross-sectional modules.
        context : SignalContext
            Shared context whose ``xsec_percentile_ranks`` dict is populated
            in-place by any cross-sectional module's ``pre_compute``.
        """
        for module in self._modules.values():
            module.pre_compute(universe_df, context)

    def compute_all(self, row: pd.Series, context: SignalContext) -> Dict[str, SignalOutput]:
        """
        Executes compute() on all registered signal modules for a given data row.
        
        Args:
            row: pandas Series representing indicator features.
            context: SignalContext containing MarketBar, Fundamentals, and Macro DTOs.
            
        Returns:
            Dict mapping signal names to SignalOutputs.
        """
        outputs = {}
        for name, module in self._modules.items():
            # Validate required features exist in the row
            for feature in module.required_features:
                if feature not in row:
                    raise ValueError(
                        f"Required feature '{feature}' for signal '{name}' is missing from row."
                    )
            outputs[name] = module.compute(row, context)
        return outputs

    def compute_all_vectorized(self, df: pd.DataFrame, context: SignalContext) -> Dict[str, pd.DataFrame]:
        """
        Executes compute_vectorized() on all registered signal modules for a universe DataFrame.
        
        Args:
            df: pandas DataFrame representing indicator features for all tickers.
            context: SignalContext containing global context data.
            
        Returns:
            Dict mapping signal names to output DataFrames (with columns score, confidence, explanation, meta_label_proba).
        """
        outputs = {}
        for name, module in self._modules.items():
            # Validate required features exist in the dataframe
            for feature in module.required_features:
                if feature not in df.columns:
                    raise ValueError(
                        f"Required feature '{feature}' for signal '{name}' is missing from DataFrame columns."
                    )
            outputs[name] = module.compute_vectorized(df, context)
        return outputs


# Default global registry singleton
global_registry = SignalRegistry()
