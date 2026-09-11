# Weekly Digest Task Tracker

- [x] **WP-A (View-tracking log)** — created `data/symbol_view_store.py` and tests.
- [x] **WP-B (Sector-gap composition)** — created `pilots/sector_gap.py` to find underrepresented sectors.
- [x] **WP-D (Scheduling skeleton)** — integrated `maybe_dispatch_weekly_digest()` into `desktop/daemon_runtime.py`.
- [x] **WP-C (Digest composer)** — created `pilots/weekly_digest.py` with 3-tier fallback ladder.
- [x] **WP-E (Delivery wiring & Docs sync)** — wired `send_alert()` into the daemon, added settings to `.env.example`, updated `CLAUDE.md` and `AGENTS.md`.
- [x] **WP-F (Webapp digest panel)** — added `GET /pilots/weekly-digest` and rendered the fallback view in the React PWA (`Marketplace.tsx` and `WeeklyDigestCard.tsx`).
