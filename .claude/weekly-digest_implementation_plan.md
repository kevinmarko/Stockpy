# Weekly Digest, Spotify-Style — Implementation Plan

> Save as `.claude/weekly-digest_implementation_plan.md`. Structured for a
> **Gemini Antigravity build / Claude Code audit** split, mirroring this
> repo's own wave-based multi-agent convention.

## Status: APPROVED — §0 completed against live code

## 0. Context & problem statement

This plan builds directly on **Today's Radar** (companion plan): the "5
names" this digest surfaces are the same composite-score-ranked data Radar
already reads, periodically packaged rather than freshly designed. Nothing
here should recompute or reinvent that ranking.

Two genuinely new pieces this feature needs:

1. **"Names you haven't looked at" requires knowing what you've looked
   at.** No view-tracking/"recently viewed" mechanism was found anywhere in
   `CLAUDE.md`'s changelog. This is real, new, minimal state — not a gap in
   an existing feature.
2. **A weekly cadence is a different order of magnitude from anything the
   scheduler currently does.** The Pipeline Schedule's interval presets
   (1m/5m/15m/30m/1h) and the daemon's own internal timer are built for
   near-continuous operation, not a once-a-week digest. We will integrate 
   a weekly check within the daemon's timer loop tracking the last send.

One genuinely good piece of news: **delivery does not need new
infrastructure.** `observability/alerts.py::send_alert()` already exists,
is already wired to ntfy push (`settings.ALERT_NTFY_TOPIC`), already
supports a `dedup_key`, and is already the established pattern for "tell the
operator something, out of band, without them having to be looking."
Reuse it.

## 1. Goal

A periodic, honestly-personalized "5 names worth a look" surfaced without
the operator having to go looking — reusing Today's Radar's ranking, a new
minimal view-tracking log, existing sector data, and the existing alert
channel. No new signal logic, no new notification infrastructure.

## 2. Explicit scope boundary

**In scope:** a minimal view-tracking log, sector-gap composition (reusing
existing FMP sector fields — diagnostic-only, per `docs/FMP_INTEGRATION.md`
§1a's own established convention that this class of feed is never a
`SignalModule`), a digest composer, delivery via the existing alert
channel, and a weekly schedule trigger.

**Out of scope, deliberately:**
- Any new `signals/` module or `SIGNAL_WEIGHTS` entry. Sector-gap analysis
  is informational.
- Real user-account/multi-user view tracking — this remains a
  single-operator (Kevin) tool; the view log is a simple append-only table,
  not an identity/auth system.
- Any change to Today's Radar's ranking logic — this plan is a consumer of
  that data, never a second implementation of it.

## 3. §0 dependency check — REQUIRED before any code, not yet done

- [x] Confirm whether Today's Radar has landed: Yes, `pilots/radar_ranking.py` exists and is functional.
- [x] Confirm no existing view-tracking/"recently viewed" mechanism exists anywhere in the repo: Confirmed, none exists.
- [x] Confirm `observability/alerts.py::send_alert()`'s exact signature: `send_alert(level, message, channels=None, extra=None, dedup_key=None)`. Active channels include console, file, discord, slack, and email based on settings.
- [x] Confirm whether the daemon's existing internal timer can reasonably host a weekly check: Yes, the daemon's `_timer_loop` in `desktop/daemon_runtime.py` can host it by tracking the last dispatch timestamp, similar to `maybe_refresh_google_trends`.
- [x] Confirm the best existing read for "current paper holdings' sector composition": We can use `data/paper_account_store.py` to get current holdings and resolve sectors via `HistoricalStore.get_fundamentals()` or the sync report.

## 4. Proposed UX

- **Primary delivery:** a push alert via `send_alert()` (ntfy, if
  configured) — five symbols, one line each, in Today's Radar's existing
  "why" template style, tagged whether each is a personalization pick
  (not-recently-viewed + high score) or a sector-gap pick.
- **Secondary/fallback:** a simple in-app "This Week's Digest" panel on the
  Marketplace hub (for whenever ntfy isn't configured, or the operator just
  wants to look later) — reuses the same composed data, no separate logic.
- **Honest fallback ladder**, since a genuinely honest "5 names" isn't
  always available:
  1. Personalized (high-score + not recently viewed) — the ideal case.
  2. If view-tracking has too little history to personalize meaningfully
     (e.g., fresh install), fall back to plain Today's Radar top-N with a
     note that personalization isn't active yet — never silently pretend
     personalization happened.
  3. If DailySignals has too few rows this cycle (the live 0-row state
     observed during earlier scoping is the realistic default case right
     now, not a rare edge case), the digest honestly reports fewer than 5
     names, or none, with a stated reason — never pads to 5 with a
     lower-confidence pick presented at the same visual weight as a real
     one.

## 5. Multi-agent build plan (Antigravity)

| Wave | Agent(s) | Work package | Files | Depends on |
|---|---|---|---|---|
| 1 | 1 | **WP-A (View-tracking log)** — a minimal, own-`Base`/table/`session_scope` store (`data/symbol_view_store.py`) recording `(symbol, viewed_at)`. | `data/symbol_view_store.py`, `tests/test_symbol_view_store.py` | — |
| 1 | 2 | **WP-B (Sector-gap composition)** — reads current paper holdings + tracked universe's sector fields, computes underrepresented sectors. | `pilots/sector_gap.py`, `tests/test_sector_gap.py` | — |
| 1 | 3 | **WP-D (Scheduling skeleton)** — weekly trigger in daemon (`desktop/daemon_runtime.py`). | `desktop/daemon_runtime.py` | — |
| 2 | 4 | **WP-C (Digest composer)** — combines Today's Radar's Top-N + WP-A's not-recently-viewed filter + WP-B's sector-gap names. | `pilots/weekly_digest.py`, `tests/test_digest_composer.py` | WP-A, WP-B |
| 3 | 5 | **WP-E (Delivery wiring & Docs sync)** — `send_alert()` integration with `dedup_key` + `settings.py` updates + Docs sync. | `settings.py`, `tests/test_digest_delivery.py`, Docs | WP-C, WP-D |
| 3 | 6 | **WP-F (Webapp digest panel)** — Marketplace-hub fallback view reusing WP-C's payload. | `webapp/src/screens/Marketplace.tsx`, `api/pilots_api.py` | WP-C |

## 6. Fabrication-risk checklist (CONSTRAINT #4)

- [ ] The digest never claims a symbol is "not recently viewed" when the
      view-tracking log has insufficient history to know that honestly — it
      falls back per §4's ladder instead.
- [ ] An empty/sparse DailySignals cycle produces an honest, shorter (or
      empty) digest, never a padded five.
- [ ] Sector-gap picks are never blended into a single opaque "score" with
      the signal-driven picks — each of the five is individually tagged
      with which rule selected it, so the operator can tell "the model
      likes this" apart from "you're underweight this sector," which are
      different claims with different evidentiary weight.
- [ ] A `send_alert()` failure (ntfy unreachable, etc.) is logged and
      surfaced in the webapp fallback panel — never silently dropped, per
      CONSTRAINT #6's dead-letter-resilience requirement.

## 7. Open questions for the operator

- Default the new digest-enabled flag `True` or `False`? Recommend `False`
  initially — this is a new recurring interruption, not a passive
  diagnostic, and deserves explicit opt-in regardless of this repo's
  general "new admin capabilities default on" convention, which was framed
  around write/execution gates, not recurring notifications.
- Weekly on a fixed day/time, or "every 7 days from last send"? Affects the
  scheduling mechanism choice in §0.
- Should the digest ever include a symbol currently outside the tracked
  universe (Screener-reachable but never signal-scored)? Recommend **no**
  for v1 — Today's Radar's own scope boundary already excluded this, and
  this plan should inherit that boundary rather than relitigate it.
