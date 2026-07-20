#!/bin/zsh

# Stock Dashboard setup.
#
# Usage:
#   ./setup.sh                # idempotent: create .venv if missing, then install/upgrade deps
#   ./setup.sh --clean        # destructive: delete .venv and rebuild from scratch
#   ./setup.sh --optional     # also install requirements-optional.txt (heavy: TensorFlow → CNN-LSTM)
#   ./setup.sh --clean --optional
#
# By default this NO LONGER deletes an existing .venv on every run — that used to
# force a full ~1.3GB re-download from PyPI each time. Now the venv is reused and
# `pip install -r` only fetches what's missing/changed, and the pip wheel cache is
# left ENABLED (no --no-cache-dir) so even a rebuild reuses already-downloaded
# wheels. Pass --clean for the rare "start completely fresh" case.

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
    echo "📦 Creating virtual environment with Python 3.12..."
    $PYTHON_EXE -m venv .venv
fi

# 4. Activate and install. No --no-cache-dir: the pip wheel cache is reused so
#    already-downloaded wheels are not re-fetched. Already-satisfied requirements
#    are skipped by pip, so a no-op run is near-instant.
echo "🛠  Installing dependencies from requirements.txt..."
source .venv/bin/activate
pip install --upgrade setuptools wheel
pip install -r requirements.txt

# 5. Optional heavy forecasting deps (TensorFlow → activates the CNN-LSTM model).
#    Off by default; opt in with --optional. See requirements-optional.txt.
if [ "$OPTIONAL" -eq 1 ]; then
    if [ -f "requirements-optional.txt" ]; then
        echo "🧠 Installing optional heavy deps (requirements-optional.txt)..."
        pip install -r requirements-optional.txt
    else
        echo "⚠️  --optional requested but requirements-optional.txt not found; skipping."
    fi
fi

echo "✅ Setup Complete! To run your dashboard, use: python3 main.py"
