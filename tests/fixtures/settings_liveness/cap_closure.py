"""Rule: closure_value -- and, just as importantly, its FALSE POSITIVE.

Three escape shapes are real captures (returned / assigned to an attribute /
registered via a decorator). The fourth function is the shape the rule must
NOT fire on: an ordinary per-call worker closure that dies with its own call
frame. If `closure_value` fired there, every ThreadPoolExecutor fan-out in
this codebase would be reported as capturing whatever settings value the
enclosing method happened to read.
"""
from concurrent.futures import ThreadPoolExecutor

from settings import settings


def make_scorer():
    """ESCAPES: the closure is returned, so `cap` outlives this call."""
    cap = settings.KELLY_CAP  # closure_value
    def scorer(x):
        return min(x, cap)

    return scorer


class Registry:
    def install(self):
        """ESCAPES: the closure is stored on an attribute."""
        target = settings.VOL_TARGET  # closure_value
        def handler(x):
            return x * target

        self.handler = handler


def wire(engine, listens_for):
    """ESCAPES: the closure is handed to a registrar decorator."""
    threshold = settings.MAX_CORRELATION  # closure_value

    @listens_for(engine, "connect")
    def on_connect(conn, rec):
        conn.threshold = threshold


def score_batch(rows):
    """DOES NOT ESCAPE: `worker` dies with this call. NOT a capture."""
    leverage = settings.MAX_LEVERAGE  # no rule must fire here
    def worker(row):
        return min(row, leverage)

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(worker, rows))
    return results
