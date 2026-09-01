# Stockpy Pilots — PWA

Mobile-first, installable React PWA for browsing and following Stockpy quant
strategy **Pilots**. Advisory and **paper-first**: "Follow" builds a gated,
human-confirmed order queue — it never places an order automatically.

Consumes `api/pilots_api.py` (FastAPI, port 8602). Runs fully offline against a
mock API layer until the backend is live.

## Run

```bash
cd webapp
npm install
npm run dev        # dev server at http://localhost:5173 (mock data by default)
npm run build      # type-check + production build -> dist/ (+ PWA service worker)
npm run preview    # serve the production build
```

No `.env` is required to run: the app defaults to the offline mock layer.

## Mock → live: the one flag

Everything is driven by `import.meta.env` (copy `.env.example` → `.env.local`):

| Var | Default | Purpose |
|-----|---------|---------|
| `VITE_USE_MOCK` | `true` | **The switch.** `true` = offline `src/api/mock.ts`; `false` = hit the live API. Strict vocabulary: `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`, case-insensitive. Anything else is a startup error, never a silent fall-back to mock. |
| `VITE_API_BASE_URL` | `http://localhost:8602` | Base URL of `api/pilots_api.py`. |
| `VITE_DATA_API_BASE_URL` | `http://localhost:8603` | Base URL of `api/data_api.py` (separate process) — serves `/data/*`, `/api/chat`, `/ws/ticks/*`. |
| `VITE_METRICS_API_BASE_URL` | `http://localhost:8604` | Base URL of `api/metrics_api.py` (separate process) — serves `/metrics/*`. |
| `VITE_CONTROL_API_BASE_URL` | `http://localhost:8601` | Base URL of `api/control_api.py` (orchestrator daemon) — serves `/status`, `/run*`, `/pipeline/*`, `/jobs*`, `/daemon/*`. |
| `VITE_API_TOKEN` | *(empty)* | Bearer token → `Authorization: Bearer <token>` (matches `STATE_API_TOKEN`). Ignored on non-loopback origins — see `src/auth/apiToken.ts`. |

All four base URLs must be absolute `http:`/`https:` URLs with no query string
or fragment (a path prefix such as `https://host.example.com/pilots` is fine —
that's the single-origin reverse-proxy case). An **empty** value means "use the
default above", not "use an empty base URL". Validation lives in
`src/config/env.ts`; a bad value renders a startup error screen instead of
failing silently.

To go live: run `uvicorn api.pilots_api:app --port 8602`, then set
`VITE_USE_MOCK=false` (and a token if the API requires one). No component code
changes — `src/api/client.ts` selects mock vs. live in one place.

## Structure

```
webapp/
├── index.html
├── vite.config.ts            # Vite + vite-plugin-pwa (manifest, service worker)
├── package.json / tsconfig.json  # Vite React-TS
├── public/                   # icon.svg, favicon.svg (PWA icons)
└── src/
    ├── main.tsx              # entry (BrowserRouter)
    ├── App.tsx               # router + bottom nav (mobile) + sidebar (desktop) + onboarding
    │                         # gate + NAV_ITEMS, the authoritative live screen/section list
    ├── theme.ts              # dark fintech tokens + validated sector/category/series palettes
    ├── index.css             # design-token CSS variables, mobile-first styles
    ├── format.ts             # $/%/date formatters
    ├── onboarding.ts         # localStorage completion marker
    ├── api/                  # types.ts, client.ts (mock/live switch), mock.ts, offlineCache.ts
    ├── hooks/                # useApi.ts, usePwaStatus.ts, useMutation.ts, ...
    ├── help/                 # helpContent.ts — TAB_HELP + GLOSSARY, rendered by TabGuide.tsx
    ├── components/           # ~22 shared UI/chart components — ui.tsx (badges, tiles, honesty
    │                         # row), charts.tsx (PerfLine/SectorDonut/Sparkline/chart chrome),
    │                         # PilotCard.tsx, Modal.tsx, Toggle.tsx, and per-feature panels
    └── screens/               # ~31 screens, one per NAV_ITEMS entry — see App.tsx for the
                                # current authoritative list rather than this (previously
                                # stale, hand-maintained) tree; group sections are primary
                                # (Dashboard/Portfolio/Activity/Agentic), research, trading,
                                # operations, and settings
```

## Design & honesty

- Dark fintech palette on `#0b0e11` base / `#12161c` surfaces (Pilots-PWA-only —
  the Streamlit operator console renders on its own light chrome, not shared).
  Its status colors — green `#10b981` growth / red `#ef4444` decline / amber
  `#f59e0b` caution — DO reuse `shared/styling.py`'s `BRAND_ACCENTS` trio exactly.
- The sector-donut categorical palette was validated with the dataviz skill's
  `validate_palette.js` against the dark surface: lightness band, chroma floor,
  and ≥3:1 contrast all pass for all 8 slots; CVD separation is a WARN (worst
  adjacent ΔE 7.5, deutan) and the normal-vision floor FAILS at one adjacent
  pair (ΔE 7.8, below the 15 floor) — legal only because `SectorDonut` always
  pairs every slice with a direct text label, so identity is never color-alone.
  See `src/theme.ts`'s module docstring for the full validation record.
- **Honesty (CONSTRAINT #4):** a Pilot that fails a validation gate renders
  `Not deployable` plainly; a `curve: null` performance response renders
  "No backtest series yet" — never a fabricated line or metric. The mock catalog
  ships two such examples (`momentum-burst` non-deployable, `value-quality`
  null curve) so the honest paths are always exercised.
