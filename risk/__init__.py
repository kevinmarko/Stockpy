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
  holding to its otherwise healthy peers. Exposed to the sizing path as a
  bounded, monotone derating multiplier
  (``risk.etf_transmission.transmission_multiplier``).

Deliberately re-exports NOTHING here: submodules are imported directly
(``from risk.etf_transmission import transmission_multiplier``) so this
package's ``__init__`` never drags a heavy third-party dependency into a
caller that only wanted one pure function -- the same footgun documented for
``forecasting/__init__.py`` in CLAUDE.md.
"""
