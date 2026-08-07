from .GlobalBackfillEngine import GlobalBackfillEngine
from ml.strategies.backfill_strategies import (
    TSMOMStrategy,
    CSMOMStrategy,
    PairsRadarStrategy,
    OptionsStrategy,
    SectorSelectionStrategy
)

# Register all models here to automatically include them in system-wide backfills
STRATEGY_REGISTRY = {
    "TSMOM": TSMOMStrategy(),
    "CSMOM": CSMOMStrategy(),
    "PAIRS_RADAR": PairsRadarStrategy(),
    "OPTIONS": OptionsStrategy(),
    "SECTOR_SELECTION": SectorSelectionStrategy(),
}

backfill_engine = GlobalBackfillEngine(registry=STRATEGY_REGISTRY)
