#!/bin/zsh

echo "🚀 Starting Stock Dashboard Setup..."

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

# 2. Clean up old environment if it exists
if [ -d ".venv" ] || [ -d "venv" ]; then
    echo "🧹 Removing old virtual environment..."
    rm -rf .venv venv
fi

# 3. Create new virtual environment
echo "📦 Creating fresh virtual environment with Python 3.12..."
$PYTHON_EXE -m venv .venv

# 4. Activate and Install
echo "🛠 Installing dependencies from requirements.txt..."
source .venv/bin/activate
pip install --upgrade setuptools wheel
pip install --no-cache-dir -r requirements.txt

echo "✅ Setup Complete! To run your dashboard, use: python3 main.py"