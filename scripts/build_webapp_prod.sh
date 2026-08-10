#!/usr/bin/env bash
set -e

# Change to the webapp directory relative to this script's location
cd "$(dirname "$0")/../webapp"

echo "Installing webapp dependencies..."
# npm ci (not install): a production build should install EXACTLY what
# package-lock.json pins and fail loudly on any lockfile/manifest mismatch,
# rather than silently updating the lockfile.
npm ci

echo "Building production webapp bundle..."
npm run build

echo "Production build complete."
