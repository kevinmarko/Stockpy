"""
InvestYo Quant Platform - ML Package
=====================================
Three-tier qlib-style architecture (no qlib dependency):

  ml/data/         Point-in-time feature store and label construction.
  ml/models/       Model ABC + concrete implementations.
  ml/strategies/   StrategySpec: links a model to a signal module.

Key modules
-----------
  ml.triple_barrier   — Lopez de Prado triple-barrier labeling (AFML Ch. 3).
  ml.meta_labeling    — MetaLabeler (binary LightGBM) + MetaLabelerRegistry.
  ml.lgbm_ranker      — LGBMCrossSectionalRanker (LambdaRank cross-sectional).
  ml.feature_engineering — PIT feature matrix builder for the LGBMRanker.
  ml.forecast_backfill   — AgenticForecastBackfiller / ForecastBackfillEngine.

Submodules are imported lazily by their consumers (matching this repo's
convention elsewhere, e.g. HistoricalStore) rather than re-exported eagerly
here, to avoid paying sklearn/pandas import cost -- and circular-import risk
-- on every bare `import ml`.
"""

