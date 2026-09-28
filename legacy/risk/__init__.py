"""
InvestYo Quant Platform - Risk Overlays Package
================================================
Cross-cutting risk overlays that sit ON TOP of the per-name sizing pipeline
(``sizing/``) rather than inside it -- measurements of structural,
non-fundamental risk that the Kelly / volatility-target formulas cannot see,
because those formulas only observe a single name's own return series.

Current contents
----------------
* ``risk/etf_transmission.py`` -- ETF-arbitrage volatility transmission
  (Ben-David, Franzoni & Moussawi 2018, *Journal of Finance*): the extra,
  non-fundamental, non-diversifiable variance a heavily ETF-wrapped
  constituent carries because ETF arbitrage propagates a shock in one
  holding to its otherwise healthy peers. Provides both the measurement
  layer (``compute_market_residual_r2``, ``compute_etf_ownership``, etc.,
  populating diagnostic dashboard columns) and the sizing-path derating
  lever built on top of it (``risk.etf_transmission.transmission_multiplier``).

Deliberately kept free of engine/orchestrator imports: every module here is
pure math over already-fetched DataFrames/dicts/scalars, so it stays
importable (and unit-testable) without pulling in the heavy
``main_orchestrator`` / ``data_engine`` chain -- I/O belongs to the callers
(``pipeline/``, ``data/``). Deliberately re-exports NOTHING here: submodules
are imported directly (``from risk.etf_transmission import
transmission_multiplier``) so this package's ``__init__`` never drags a
dependency into a caller that only wanted one pure function -- the same
footgun documented for ``forecasting/__init__.py`` in CLAUDE.md.
"""
