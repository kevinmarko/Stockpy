import re

with open("scripts/refresh_validations.py", "r") as f:
    content = f.read()

adapter_code = """
def _build_ungateable_adapter(reason: str) -> Callable[[pd.Series], Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]]:
    \"\"\"Returns a dummy adapter that always raises RuntimeError with the
    given reason, forcing the strategy to gracefully record an ERROR
    status during validation.
    \"\"\"
    def adapter(_spy: pd.Series) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
        raise RuntimeError(f"UNGATEABLE_DATA_GAP: {reason}")
    return adapter

STRATEGY_REGISTRY: Dict[str, Tuple[Callable, float, List[str]]] = {
"""

content = content.replace("STRATEGY_REGISTRY: Dict[str, Tuple[Callable, float, List[str]]] = {", adapter_code)

with open("scripts/refresh_validations.py", "w") as f:
    f.write(content)
