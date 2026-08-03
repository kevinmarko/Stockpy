"""Rules: decorator_arg, default_arg.

Both are evaluated in the ENCLOSING scope at `def` time, not on call -- which
is why scope_of() must skip the function they decorate/belong to rather than
reporting them as reads inside it.
"""
from settings import settings


def retry(times):
    def _wrap(fn):
        return fn

    return _wrap


@retry(settings.HMM_N_STATES)  # decorator_arg
def do_work():
    return None


def take_top(n: int = settings.PILOTS_TOP_N):  # default_arg
    return n
