"""InvestYo Quant Platform - Symbol Rating Package
===================================================
Durable per-symbol rating history, built on top of the platform's existing
per-cycle 0-100 ``final_score`` / 4-tier Action Signal
(``strategy_engine.py::evaluate_security()``). Classifies each cycle's score
as GOOD/BAD (``rating/symbol_rating.py``) and persists that classification
(``rating/symbol_rating_store.py``) so a later cycle can ask "how many
consecutive BAD cycles has this symbol had" -- the basis for an opt-in
auto-drop-from-tracking rule owned by a downstream pipeline-wiring task, not
by this package itself.

Deliberately re-exports NOTHING here, mirroring ``risk/__init__.py``'s own
stated rationale: submodules are imported directly (``from
rating.symbol_rating import classify_tier``, ``from rating.symbol_rating_store
import SymbolRatingStore``) so this package's ``__init__`` never drags a
dependency (SQLAlchemy, ``db_config``) into a caller that only wanted the
pure classification logic.
"""
