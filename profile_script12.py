import cProfile
import pstats
from main import run_once, _load_dotenv, ENV_PATH

_load_dotenv(ENV_PATH, override=False)

# First run to compile Numba functions
run_once(force_account=False)

# Second run for actual profiling
profiler = cProfile.Profile()
profiler.enable()
run_once(force_account=False)
profiler.disable()

stats = pstats.Stats(profiler).sort_stats('cumtime')
stats.print_stats(30)
