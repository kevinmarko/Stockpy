# Task Tracker: Options Matrix POP truncation bug + VolSurface3D skew drift-risk

Branch: `fix-options-pop-and-vol-skew-drift`

- [x] Explore `OptionsMatrix.tsx` DetailSheet's POP calculation and
      `optionsMath.ts`'s existing helpers.
- [x] Explore `VolSurface3D.tsx`'s `calculateSurfaceMetrics` and confirm
      (via grep) it's the only real call site, plus confirm the sibling
      `VolSurfaceView.tsx`'s real-backend-value rendering pattern.
- [x] Numerically verify the proposed closed-form POP fix against
      independently-derived reference values in a standalone Node script
      (3 cases: single-breakeven credit spread, near-the-money long call,
      multi-breakeven iron condor) before writing it into the codebase.
- [x] Sync branch from `main` (`git fetch && git rebase`/fresh branch off
      up-to-date `main`).
- [x] `optionsMath.ts`: extract `filterValidLegs`/`evaluatePayoffAt`
      helpers (dedup), add `computeProbabilityOfProfit`.
- [x] `OptionsMatrix.tsx`: wire `DetailSheet`'s `popPercent` to the new
      function; drop the now-unused `normalProbabilityDensity` import.
- [x] `VolSurface3D.tsx`: `calculateSurfaceMetrics(mesh, volResponse?)`
      prefers the real backend `skew_25delta`, adds `skew25dIsReal`,
      `skew25d` becomes `number | null`; update the render block (null
      handling + "(chain)"/"(proxy)" provenance label) and the call site.
- [x] Add test coverage: `optionsMath.test.ts` (POP correctness incl. the
      confirmed discrepancy case, multi-breakeven, degenerate inputs,
      bounds), `VolSurface3D.test.tsx` (real-vs-proxy preference, honest
      null on absent real value).
- [x] `npm run --prefix webapp typecheck` — clean.
- [x] Targeted vitest runs (`optionsMath.test.ts`,
      `VolSurface3D.test.tsx`, `OptionsMatrix.test.tsx`) — all passing.
- [x] Full `npm test` (webapp) — 169 files / 1869 tests, no regressions.
- [x] PR artifacts (`.claude/options_pop_vol_skew_fix_*`) committed per
      CLAUDE.md's PR-artifact convention.
- [x] Open PR against `main` — [PR #895](https://github.com/kevinmarko/Stockpy/pull/895).
- [x] `docs/known_issues/options_matrix_pop_truncation_and_vol_skew_drift.md`
      write-up, with the real PR link filled in, plus a
      `docs/known_issues/README.md` index row.
