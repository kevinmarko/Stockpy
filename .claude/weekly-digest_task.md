# Weekly Digest Task Tracker

- [x] **WP-0 (Scaffold)** — created `pilots/digest_models.py` with `DigestPayload` and `DigestItem`.
- [x] **WP-A (View-tracking log)** — created `data/symbol_view_store.py` and tests.
- [x] **WP-B (Sector-gap composition)** — created `pilots/sector_gap.py` to find underrepresented sectors.
- [x] **WP-D (Scheduling skeleton)** — added `maybe_dispatch_weekly_digest()` to `desktop/daemon_runtime.py`.
- [x] **WP-C (Digest composer)** — created `pilots/weekly_digest.py` to tie WP-A and WP-B together and create a `DigestPayload`.
- [x] **WP-E (Delivery wiring)** — updated `maybe_dispatch_weekly_digest()` in the daemon to call `send_alert()`.
- [x] **WP-F (Webapp digest panel)** — added `GET /pilots/weekly-digest` and rendered the view in `Marketplace.tsx`.
- [x] **WP-G (Docs sync)** — added settings to `.env.example`, updated `CLAUDE.md` and `AGENTS.md`.
