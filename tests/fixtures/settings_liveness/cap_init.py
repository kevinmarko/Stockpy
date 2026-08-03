"""Rules: init_self_assign, init_body, indirect_init_helper_d1.

The indirect case is the single most important shape in the classifier: a
naive "only reads lexically inside __init__ capture" rule would report
MARKET_DATA_PROVIDER here as live-safe, when in fact it is resolved once and
frozen into a long-lived attribute.
"""
from settings import settings


class Engine:
    def __init__(self):
        self.cap = settings.MAX_LEVERAGE  # init_self_assign
        threshold = settings.MAX_CORRELATION  # init_body
        self._threshold = threshold
        self.provider = self._select_provider()

    def _select_provider(self):
        # indirect_init_helper_d1 -- reached one call hop from __init__.
        return (settings.MARKET_DATA_PROVIDER or "").strip().lower()
