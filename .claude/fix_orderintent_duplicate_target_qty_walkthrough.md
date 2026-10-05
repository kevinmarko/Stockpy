# Walkthrough: duplicate OrderIntent.target_qty

- `execution/broker_base.py`: one `target_qty` declaration remains, in its original position after `time_in_force`, with the
  telemetry comment that used to sit on the duplicate. `dataclasses.fields(OrderIntent)` is identical before and after.
- `tests/test_broker_base_dataclass_fields.py`:
  - an AST guard against repeated field annotations in any broker_base class;
  - a pin on OrderIntent's field order;
  - a check that the default is None.
- No behavior change. Producers in main_orchestrator.py and the existing target_qty tests in
  tests/test_execute_broker_orders.py are unchanged and pass.
