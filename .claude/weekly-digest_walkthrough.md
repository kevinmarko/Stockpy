# Weekly Digest Walkthrough

## What was built
Implemented the "Weekly Digest, Spotify-Style" feature using a 6-agent wave-based build.
The feature surfaces 5 ticker symbols the operator hasn't recently viewed, ranked by Today's Radar score, along with missing sector-gap representation. It delivers this via an out-of-band alert on a weekly cadence and provides an in-app fallback panel.

### Components
1. **View Tracking Store (`data/symbol_view_store.py`)**: A durable SQLAlchemy table that records symbols the operator has recently viewed, avoiding recommending the same symbols back-to-back.
2. **Sector Gap Composition (`pilots/sector_gap.py`)**: A read-only diagnostic module that cross-references current open paper positions against the tracked universe's fundamental sectors to find gaps.
3. **Digest Composer (`pilots/weekly_digest.py`)**: Aggregates the Radar feed, view store, and sector gaps into a 5-item, 3-tier fallback ladder ("Personalized", "Sector Gap", "Today's Radar").
4. **Daemon Integration (`desktop/daemon_runtime.py`)**: Added a 168-hour cadence trigger to the background orchestrator timer loop, with `dedup_key` safely isolating duplicate sends.
5. **Webapp & API (`api/pilots_api.py`, `webapp/`)**: Created a new `/pilots/weekly-digest` endpoint and added a "This Week's Digest" card on the `Marketplace.tsx` dashboard.
6. **Documentation & Settings**: Updated `CLAUDE.md`/`AGENTS.md` and added `WEEKLY_DIGEST_ENABLED` configuration.

## What was tested
- **View Store Isolation**: The new DB store was isolated in tests with a new `conftest.py` fixture.
- **Fallback Ladder Logic**: Tested the composer's graceful degradation when view history is empty or Radar is sparse.
- **Daemon Scheduling**: Mocked the timer loop to ensure `send_alert` is properly throttled, deduped, and exceptions are safely swallowed per CONSTRAINT #6.
- **API and UI**: The new endpoint and `WeeklyDigestCard.tsx` React component were tested, ensuring parity between live and mock modes.
