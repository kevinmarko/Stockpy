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
| `VITE_USE_MOCK` | `true` | **The switch.** `true` = offline `src/api/mock.ts`; `false` = hit the live API. |
| `VITE_API_BASE_URL` | `http://localhost:8602` | Base URL of `api/pilots_api.py`. |
| `VITE_API_TOKEN` | *(empty)* | Bearer token → `Authorization: Bearer <token>` (matches `STATE_API_TOKEN`). |

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
    ├── App.tsx               # router + bottom nav (mobile) + sidebar (desktop) + onboarding gate
    ├── theme.ts              # dark fintech tokens + validated donut palette
    ├── index.css             # design-token CSS variables, mobile-first styles
    ├── format.ts             # $/%/date formatters
    ├── onboarding.ts         # localStorage completion marker
    ├── api/
    │   ├── types.ts          # TS mirror of api/pilots_api.py response shapes
    │   ├── client.ts         # typed client + USE_MOCK switch (api: typeof liveApi)
    │   ├── mock.ts           # realistic offline fixtures for every endpoint
    │   └── offlineCache.ts   # localStorage GET cache (offline fallback)
    ├── hooks/
    │   ├── useApi.ts         # async loader (distinguishes honest 404 from error)
    │   └── usePwaStatus.ts   # service-worker registration/update state
    ├── components/
    │   ├── ui.tsx            # badges, honesty row, tiles, loading/error/empty states
    │   ├── charts.tsx        # PerfLine, SectorDonut, Sparkline (Recharts)
    │   ├── PilotCard.tsx     # marketplace rail cards
    │   ├── RangeToggle.tsx   # 1W/1M/3M/6M/1Y/2Y segmented control
    │   ├── ActivityFeed.tsx  # polling ENTER/EXIT/REWEIGHT feed
    │   ├── NotebookMLExport.tsx  # copy/download portfolio as JSON
    │   └── PwaStatusDrawer.tsx   # ⚙ service-worker status sheet (every screen)
    └── screens/
        ├── Onboarding.tsx    # 3 steps: Pilot → brokerage (paper-first) → amount
        ├── Dashboard.tsx     # `/` — draggable widget grid
        ├── Marketplace.tsx   # `/marketplace` — Top Performers / Most Popular / category rails
        ├── PilotDetail.tsx   # `/pilots/:id` — perf chart, holdings, donut, trades
        ├── Portfolio.tsx     # `/portfolio` — equity tiles, curve, active follows
        ├── Comparison.tsx    # `/compare` — multi-Pilot comparison
        ├── SymbolDetail.tsx  # `/symbol/:ticker` — per-symbol context/forecast/options
        ├── Activity.tsx      # `/activity` — full activity feed
        ├── Models.tsx        # `/models` — ML model registry + honest CPCV metrics
        ├── PairsRadar.tsx    # `/pairs` — cointegrated pairs radar
        └── FollowModal.tsx   # amount → planned_intents preview + gating notice
```

## Design & honesty

- Dark fintech palette reused from Stockpy's operator console: green `#10b981`
  growth / red `#ef4444` decline / amber `#f59e0b` caution, on `#0b0e11` base /
  `#12161c` surfaces.
- The sector-donut categorical palette was validated with the dataviz skill's
  `validate_palette.js` against the dark surface (all six checks pass; worst
  adjacent CVD ΔE 23.7). See `src/theme.ts`.
- **Honesty (CONSTRAINT #4):** a Pilot that fails a validation gate renders
  `Not deployable` plainly; a `curve: null` performance response renders
  "No backtest series yet" — never a fabricated line or metric. The mock catalog
  ships two such examples (`momentum-burst` non-deployable, `value-quality`
  null curve) so the honest paths are always exercised.
```
