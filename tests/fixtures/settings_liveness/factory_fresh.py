"""Read form 4: a guard/dependency factory taking the field NAME as a param.

The read site is a dynamic getattr, so no per-key attribution is possible
there -- but the factory CALL passes a string constant, so the key is
statically knowable. The inner function runs per request, so this is fresh.
Mirrors api/auth.py::make_command_token_guard and
api/data_api.py::require_ai_capability_enabled.
"""
from settings import settings


def make_flag_guard(flag_name, label):
    def _guard():
        if not getattr(settings, flag_name, False):
            raise RuntimeError(f"{label} is disabled ({flag_name}=false).")

    return _guard


require_dry_run = make_flag_guard("DRY_RUN", "Dry run")
