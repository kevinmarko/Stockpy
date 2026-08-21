# Add Light Theme to Webapp

Create a robust light theme for the Stockpy Pilots webapp by migrating our hardcoded static palette to dynamic CSS variables governed by a user-selectable theme toggle, while respecting the existing `theme.ts` and Recharts constraints.

## User Review Required

> [!IMPORTANT]
> - **Categorical Palette Contrast**: The current `SECTOR_PALETTE`, `SERIES_PALETTE`, and `CATEGORY_PALETTE` in `theme.ts` were explicitly validated for contrast against the dark background `#12161c`. For the light mode, we will generate corresponding slightly darker variants (e.g. converting `sky-400` to `sky-600`) to ensure legibility against `#ffffff` backgrounds.
> - **Recharts CSS Variable Support**: The comment in `theme.ts` explicitly claims Recharts needs "JS color values, not CSS vars." However, modern SVG `fill` and `stroke` attributes (which Recharts uses) fully support `var(--custom-property)`. If Recharts fails to render `var(--growth)` during verification, we will fall back to a React `ThemeContext` object injection.

## Open Questions

None at this time.

## Proposed Changes

### `webapp/src/index.css`
Update the root styles to use `data-theme` for toggling instead of hardcoded dark mode colors.
- Define a `:root` scope representing the baseline dark variables (preserving existing hex values).
- Define a `:root[data-theme='light']` scope with inverted colors (e.g., `--surface: #ffffff`, `--text-primary: #0f172a`).
- Define CSS variables for the categorical/series palettes (`--sector-0`, `--category-momentum`, etc.) in both light and dark scopes to maintain appropriate contrast.
- Update `color-scheme` to `light dark` so native inputs respect the active theme.

### `webapp/src/theme.ts`
Refactor to map the static JS object to CSS variables instead of hardcoded hex values, making it dynamically reactive to the `index.css` theme without rebuilding context across 50+ files.

#### [MODIFY] [theme.ts](file:///Users/kevinlee/Stockpy-live/webapp/src/theme.ts)
- Replace static hex assignments with `var(...)` strings (e.g., `base: "var(--base)", growth: "var(--growth)"`).
- Update the arrays `SECTOR_PALETTE`, `SERIES_PALETTE`, and the object `CATEGORY_PALETTE` to map to their new respective CSS variables.
- Preserve the existing validation docstrings, adding a note about the light mode variants and the migration to CSS vars.

### Theme Provider & Toggle

#### [NEW] [ThemeContext.tsx](file:///Users/kevinlee/Stockpy-live/webapp/src/context/ThemeContext.tsx)
- Create a simple context provider managing `theme` state (`light`, `dark`, or `system`).
- Uses a `useEffect` to append the `data-theme` attribute to `document.documentElement` dynamically.
- Persists user preference via `localStorage`.

#### [NEW] [ThemeToggle.tsx](file:///Users/kevinlee/Stockpy-live/webapp/src/components/ThemeToggle.tsx)
- Create a visual toggle button (a sun/moon icon) using `lucide-react`.

#### [MODIFY] [App.tsx](file:///Users/kevinlee/Stockpy-live/webapp/src/App.tsx)
- Wrap the main application layer with `<ThemeProvider>`.

#### [MODIFY] [TopStatusBar.tsx](file:///Users/kevinlee/Stockpy-live/webapp/src/components/TopStatusBar.tsx)
- Insert the `<ThemeToggle />` into the quick actions area alongside the existing `<DensityToggle />`.

## Verification Plan

### Automated Tests
- Run `npm run test` in `webapp/` to ensure no components (especially `theme.test.ts` and `charts.test.tsx`) break with the string transition from `#hex` to `var(--...)`.

### Manual Verification
- Run `npm run dev` in `webapp/`.
- Open the application and toggle between Light and Dark mode using the new button.
- Check the Dashboard and a Symbol Detail page to confirm that the `Recharts` graphs natively resolve the `var(...)` colors and apply the correct semantic, sector, and series colors.
- Ensure text contrast is accessible in both modes.
