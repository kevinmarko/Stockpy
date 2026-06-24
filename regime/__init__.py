"""
InvestYo Quant Platform - Regime Detection Package
=====================================================
Statistical (Gaussian HMM) regime detection as a second opinion to the
rules-based regime classification in macro_engine.py.
"""

from regime.hmm_regime import HMMRegimeDetector, build_feature_matrix

__all__ = ["HMMRegimeDetector", "build_feature_matrix"]
