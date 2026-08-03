"""Rule: cross_module_init_helper.

`build_engine` reads DATABASE_URL and DB_POOL_SIZE freshly, but the object it
returns is stored on `self` and outlives the call, so both keys are captured
at THIS call site even though neither name appears in this file.
"""
from crossmod_helper import build_engine


class Store:
    def __init__(self):
        self.engine = build_engine()
