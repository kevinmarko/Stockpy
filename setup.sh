#!/bin/zsh

# Stock Dashboard setup.
#
# Usage:
#   ./setup.sh                # idempotent: create .venv if missing, then install/upgrade deps
#   ./setup.sh --clean        # destructive: delete .venv and rebuild from scratch
#   ./setup.sh --optional     # also install requirements-optional.txt (heavy: TensorFlow → CNN-LSTM)
#   ./setup.sh --clean --optional
#
# Uses `uv` (https://astral.sh/uv) instead of stdlib venv + pip to create and
# populate .venv — same .venv layout and requirements.txt as the source of
# truth, just a much faster resolver/installer/cache underneath, so this no
# longer requires a manual `source .venv/bin/activate` step either. `--seed`
# keeps a real `pip` inside .venv (uv-created venvs are pip-less by default)
# so every "pip install X" remediation hint elsewhere in this repo still
# works unmodified after activation.
#
# By default this NO LONGER deletes an existing .venv on every run — that used to
# force a full ~1.3GB re-download from PyPI each time. Now the venv is reused and
# `uv pip install -r` only fetches what's missing/changed, reusing uv's own
# package cache. Pass --clean for the rare "start completely fresh" case.

echo "🚀 Starting Stock Dashboard Setup..."

CLEAN=0
OPTIONAL=0
for arg in "$@"; do
    case "$arg" in
        --clean|--force) CLEAN=1 ;;
        --optional)      OPTIONAL=1 ;;
        *) echo "⚠️  Unknown argument: $arg (valid: --clean, --optional)" ;;
    esac
done

# 0. uv is the package manager this script drives .venv with.
if ! command -v uv >/dev/null 2>&1; then
    echo "❌ uv not found. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "   (or: brew install uv), then re-run ./setup.sh"
    exit 1
fi

# 1. Ensure we are using Python 3.12 (Stable)
# This assumes you installed it via brew install python@3.12 or python@3.13
PYTHON_EXE="/opt/homebrew/opt/python@3.12/bin/python3.12"

if [ ! -f "$PYTHON_EXE" ]; then
    PYTHON_EXE=$(which python3.12)
     if [ -z "$PYTHON_EXE" ]; then
        echo "❌ Python 3.12 or 3.13 not found. Please run: brew install python@3.12 or brew install python@3.13"
        exit 1
    fi
fi

# 2. Only rebuild the environment when explicitly asked (--clean). A stray legacy
#    `venv/` dir (non-dot) is always removed so it can't shadow `.venv`.
# NOTE: The platform rejects the 'pgsqlite' library in favor of Python's native sqlite3 module
# alongside SQLAlchemy and psycopg2-binary. QuantFAA and arch are mandatory for risk evaluation.
if [ "$CLEAN" -eq 1 ]; then
    echo "🧹 --clean: removing existing virtual environment(s)..."
    rm -rf .venv venv
elif [ -d "venv" ]; then
    echo "🧹 Removing stray legacy 'venv/' (canonical env is '.venv')..."
    rm -rf venv
fi

# 3. Create the virtual environment only if it doesn't already exist.
if [ -d ".venv" ]; then
    echo "♻️  Reusing existing .venv (pass --clean to rebuild from scratch)."
else
    echo "📦 Creating virtual environment with Python 3.12 (via uv)..."
    uv venv .venv --python "$PYTHON_EXE" --seed
fi

# 4. Install deps straight into .venv — no activation needed. Already-satisfied
#    requirements are skipped, so a no-op run is near-instant.
echo "🛠  Installing dependencies from requirements.txt (via uv)..."
uv pip install --python .venv/bin/python3 -r requirements.txt

# 5. Optional heavy forecasting deps (TensorFlow → activates the CNN-LSTM model).
#    Off by default; opt in with --optional. See requirements-optional.txt.
if [ "$OPTIONAL" -eq 1 ]; then
    if [ -f "requirements-optional.txt" ]; then
        echo "🧠 Installing optional heavy deps (requirements-optional.txt)..."
        uv pip install --python .venv/bin/python3 -r requirements-optional.txt
    else
        echo "⚠️  --optional requested but requirements-optional.txt not found; skipping."
    fi
fi

echo "✅ Setup Complete! To run your dashboard, use: python3 main.py"
