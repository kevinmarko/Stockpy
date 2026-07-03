import pandas as pd
import numpy as np
import pathlib
from unittest.mock import patch, MagicMock
import scripts.refresh_validations as _rv

idx = pd.bdate_range("2015-01-01", periods=252*5)
prices = 100.0 * np.exp(np.random.randn(len(idx)) * 0.01).cumprod()
spy = pd.Series(prices, index=idx)
try:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        with patch("scripts.refresh_validations._download_spy", return_value=spy), \
             patch("scripts.refresh_validations.TieredCostModel", return_value=MagicMock()):
            results = _rv.run_validations(
                strategies=["__no_such_strategy__"],
                output_dir=pathlib.Path(td),
            )
    r = results.get("__no_such_strategy__", {})
    ok7 = r.get("deployable") is False and "error" in r
    print("ok7:", ok7, "results:", results)
except Exception as e:
    print("Exception:", e)
