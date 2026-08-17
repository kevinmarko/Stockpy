# Persistent Data Refresh & Schedule Control Fix Walkthrough

## Summary of Changes

### 1. Dynamic Settings Hot-Reloading in Daemon Runtime
- **`settings.py`**: Changed `RUNTIME_FLAGS_REFRESH_ENABLED` default to `True`.
- **`desktop/daemon_runtime.py`**: Added `self.maybe_refresh_settings()` inside `_timer_loop()` on each wake-up and iteration before evaluating `is_automatic_run_gated()`. Changes to `ORCHESTRATOR_EXTENDED_HOURS_ONLY`, `ORCHESTRATOR_INTERVAL_SECONDS`, etc. are now picked up live.

### 2. User Interface Enhancements in Pilots PWA (`webapp/src/screens/SettingsData.tsx`)
- **Master Schedule Switch**: Added an explicit **"Enable Scheduled Pipeline Runs"** toggle. Toggling OFF immediately sets the interval to `0` and parks the background daemon timer.
- **Interval Presets & Custom Input**: Added fast preset buttons (`1m`, `5m`, `15m`, `30m`, `1h`) and made custom seconds input visible and editable only when scheduled runs are enabled.
- **Positive Market Hours Wording**: Rephrased the toggle to **"Limit to Extended Market Hours (4 AM – 8 PM ET)"** with clear copy: *"Only run automatic pipeline cycles during extended market hours (4:00 AM – 8:00 PM ET, weekdays). When disabled, automatic runs occur 24/7."*
- **Developer Jargon Removed**: Removed the raw code snippets and confusing developer caveats referencing process restarts.
- **Status Indicator**: Added real-time badge indicating `Active (<N>s)` or `Paused (0s)`.

## Verification Results

### Backend Python Suite
- Ran `pytest tests/test_orchestrator_daemon.py tests/test_daemon_runtime.py tests/test_pilots_api.py -v`:
  - **148 passed** in 13.91s.

### Frontend Webapp Suite
- Ran `npm run --prefix webapp typecheck`:
  - **Clean (0 errors)**.
- Ran `npm run --prefix webapp test` (Vitest):
  - **50 test files passed, 470 tests passed** (including new `SettingsData.test.tsx`).
