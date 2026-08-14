# Walkthrough: Fix Paper Broker Settings Icon Glitch

Fixed the visual bug on the webapp Settings ("Tunables & Modules") screen where an unstyled raw SVG icon was expanding to the full width of the container.

## Changes

### Webapp

#### [SettingsModules.tsx](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/debug_settings_icon_glitch/webapp/src/screens/SettingsModules.tsx)
- Replaced `PaperBrokerLink`'s unstyled `<svg>` and `settings-link-row` markup with the standard card component structure (`className="card card-pad"`).
- Integrated `useApi(() => api.getPaperBrokerSettings(), [])` to display dynamic field count summary.
- Standardized destination route to `/settings/paper-broker`.

#### [SettingsModules.test.tsx](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/debug_settings_icon_glitch/webapp/src/screens/SettingsModules.test.tsx)
- Added unit test suite validating that all settings module links (including Paper Broker) render correctly.

## Verification Results

### Automated Tests
- `npm run --prefix webapp typecheck` passed cleanly.
- `npm run --prefix webapp test` ran 135 test files and passed all 1,540 tests.
