import inspect
import api.pilots_api
import api.data_api
import settings_keysets
from pilots.feature_flags import FEATURE_FLAG_KEYS

def test_all_dangerous_keys_are_feature_flags():
    """Every dangerous key must be surfaced in the Feature Flags screen."""
    assert settings_keysets.DANGEROUS_KEYS.issubset(FEATURE_FLAG_KEYS), \
        "Some DANGEROUS_KEYS are missing from FEATURE_FLAG_KEYS"

def test_all_require_enabled_endpoints_are_feature_flags():
    """Any function named require_*_enabled in our APIs must correspond to a flag in FEATURE_FLAG_KEYS."""
    
    missing_flags = []
    
    for module in [api.pilots_api, api.data_api]:
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("require_") and name.endswith("_enabled"):
                source = inspect.getsource(func)
                import re
                match = re.search(r"settings\.([A-Z0-9_]+_ENABLED)", source)
                if match:
                    flag_name = match.group(1)
                    if flag_name not in FEATURE_FLAG_KEYS:
                        missing_flags.append(f"{name} -> {flag_name}")
                else:
                    missing_flags.append(f"{name} -> COULD NOT PARSE FLAG")
                    
    assert not missing_flags, \
        f"Found API gate functions whose flags are not in FEATURE_FLAG_KEYS: {missing_flags}"
