# Fix: duplicate `target_qty` on OrderIntent

`execution/broker_base.py::OrderIntent` annotated `target_qty: Optional[float] = None` twice. Python accepts
this: the field keeps its first position (after `time_in_force`) and takes the last default. Both defaults
were `None`, so there is no live impact, but editing only one declaration later could change behavior silently.

Plan: delete the second declaration and move its explanatory comment onto the first. The field order and
default stay the same. Add `tests/test_broker_base_dataclass_fields.py` with three tests:
- an AST guard: no class in broker_base.py declares a field twice;
- a pin on OrderIntent's field order;
- a check that `target_qty` defaults to None.

Docs: no CLAUDE.md or architecture change (no behavior change).
