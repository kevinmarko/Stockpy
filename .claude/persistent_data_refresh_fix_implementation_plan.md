# Persistent Data Refresh & Schedule Control Fix Implementation Plan

## Problem Summary
The operator reported that data continues to refresh even when they believed background refresh features were disabled, and the Settings → Data & Automation screen displayed confusing developer jargon and warning banners about requiring process restarts.

## Root Causes Identified
1. **No Explicit Master Schedule Toggle**: Disabling scheduled pipeline execution required the operator to manually type `0` into the interval input box and click save.
2. **Inverted Mental Model for Extended Hours Only**: The "Extended Market Hours Only" toggle, when turned OFF, meant "do not restrict runs to market hours" -> "run 24/7 constantly", which caused unexpected continuous background refreshes outside market hours.
3. **Stale Running State / No Hot Reloading**: `settings.RUNTIME_FLAGS_REFRESH_ENABLED` defaulted to `False`, meaning the background orchestrator daemon did not reload `output/runtime_flags.json` changes made from the UI without a full process restart.
4. **Developer Jargon in UI**: Confusing caveat notes referencing internal daemon mechanics were displayed directly in the user-facing interface.

## Changes Implemented

### 1. Backend Runtime Flags Auto-Refresh (`settings.py`, `desktop/daemon_runtime.py`)
- Changed `settings.RUNTIME_FLAGS_REFRESH_ENABLED` default from `False` to `True` (aligned with repo convention for admin/ops capabilities).
- In `desktop/daemon_runtime.py::_timer_loop()`, added `self.maybe_refresh_settings()` on loop iteration / wake-up, ensuring changes written to `output/runtime_flags.json` (such as `ORCHESTRATOR_EXTENDED_HOURS_ONLY` or `ORCHESTRATOR_INTERVAL_SECONDS`) take effect immediately in the running daemon.

### 2. UI / UX Redesign in Pilots PWA (`webapp/src/screens/SettingsData.tsx`)
- Added explicit master toggle: **"Enable Scheduled Pipeline Runs"**:
  - Toggled ON: sets interval to last saved value (or 300s default).
  - Toggled OFF: sets interval to 0 immediately (parking the daemon timer).
- Added quick interval presets: `1m (60s)`, `5m (300s)`, `15m (900s)`, `30m (1800s)`, `1h (3600s)`.
- Replaced confusing developer caveats with clean, positive phrasing for **"Limit to Extended Market Hours (4 AM – 8 PM ET)"**:
  - *"Only run automatic pipeline cycles during extended market hours (4:00 AM – 8:00 PM ET, weekdays). When disabled, automatic runs occur 24/7."*
- Added real-time status badge (`Active (300s)` vs `Paused (0s)`).

### 3. Unit & Integration Tests
- Updated `tests/test_orchestrator_daemon.py` to verify default `True` behavior for `RUNTIME_FLAGS_REFRESH_ENABLED`.
- Created `webapp/src/screens/SettingsData.test.tsx` verifying schedule toggle, interval presets, and extended hours toggle.

### 4. Documentation
- Updated `CLAUDE.md`, `AGENTS.md`, and `docs/architecture/webapp-and-gui.md`.
