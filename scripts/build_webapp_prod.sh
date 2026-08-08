#!/usr/bin/env bash
set -e

# Change to the webapp directory relative to this script's location
cd "$(dirname "$0")/../webapp"

echo "Installing webapp dependencies..."
npm install

echo "Building production webapp bundle..."
npm run build

echo "Production build complete."
