# Project: Explain This Ticker & Universe Transparency

## Architecture
- **Backend Layer**:
  - `data/fmp_client.py`: Gated wrapper `company_profile(symbol)` with `FMP_PROFILE_ENABLED` check, logging missing data, returning `None` on failure/disabled.
  - `settings.py`: Capability switch `FMP_PROFILE_ENABLED: bool = Field(default=True, ...)`.
  - `shared/env_io.py`: Include `FMP_PROFILE_ENABLED` in `ALLOWED_KEYS`.
  - `scripts/verify_fmp_profile.py`: Operator verification script (exit codes: 0=pass, 1=fail, 2=config error).
  - `api/data_api.py`: Endpoint `GET /data/explain/{symbol}` aggregating FMP profile, sync-report provenance, `DailySignals` factor breakdown, and `price_bars` history status.
- **Frontend Layer**:
  - `webapp/src/api/types.ts`: TypeScript contracts for `ExplainTickerResponse`.
  - `webapp/src/api/client.ts` & `mock.ts`: Mock/live API client parity.
  - `webapp/src/context/ExplainTickerContext.tsx`: Global context providing `useExplainTicker()`.
  - `webapp/src/components/ExplainTickerButton.tsx`: Reusable (ⓘ) button with `stopPropagation`.
  - `webapp/src/components/ExplainTickerDrawer.tsx`: Reusable 4-section slide-over panel with honest empty states.
  - `webapp/src/screens/UniverseTransparency.tsx`: Global universe coverage view at `/universe` with 6-KPI summary, filter tabs, and stream checklist.
  - `webapp/src/App.tsx` & `navigation.tsx`: Global providers, route registration, and navigation links.
- **Testing & Integrity**:
  - Offline pytest suites for backend.
  - Vitest + React Testing Library for frontend components.
  - Full typecheck (`tsc --noEmit`).
  - Strict non-fabrication: zero synthetic scores, honest empty/unavailable copy.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | FMP Profile Wrapper | `company_profile(symbol)` in `data/fmp_client.py` gated by `FMP_PROFILE_ENABLED` | M1 (Backend) | survey / R1 |
| 2 | FMP Profile Verifier | `scripts/verify_fmp_profile.py` mirroring `verify_fmp_bars.py` | M1 (Backend) | survey / R1 |
| 3 | Explain Endpoint | `GET /data/explain/{symbol}` in `api/data_api.py` with 4 honest sections | M1 (Backend) | survey / R2 |
| 4 | Backend Unit Tests | `tests/test_fmp_client.py` and `tests/test_data_api.py` additions | M1 (Backend) | survey / R5 |
| 5 | PWA API Types & Mock | `ExplainTickerResponse` in `types.ts`, `client.ts`, and `mock.ts` | M2 (Frontend) | survey / R3, R4 |
| 6 | Explain Ticker Context | Global `ExplainTickerContext` and `useExplainTicker()` hook | M2 (Frontend) | survey / R3 |
| 7 | Ticker Info Button (ⓘ) | Reusable `ExplainTickerButton` with `stopPropagation` across app screens | M2 (Frontend) | survey / R3 |
| 8 | Explain Slide-over Drawer | 4-section `ExplainTickerDrawer` with zero-fabrication empty states | M2 (Frontend) | survey / R3 |
| 9 | Universe Transparency Screen | Dedicated `/universe` screen consuming existing `GET /data/sync-report` | M2 (Frontend) | survey / R4 |
| 10 | Frontend Component Tests | Vitest tests for Drawer, Buttons, and Universe Transparency | M2 (Frontend) | survey / R5 |
| 11 | Documentation Synchronization | Keep `CLAUDE.md`, `AGENTS.md`, and docs in sync | M3 (Verification) | survey / R5 |
| 12 | Adversarial Integrity Audit | Zero-fabrication check, stress tests, and forensic audit | M3 (Verification) | survey / Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Backend & Data Layer | FMP wrapper, settings, verifier script, Explain API endpoint, and pytest tests | none | DONE (155 passing tests) |
| M2 | Frontend PWA Implementation | Types, client/mock parity, context, drawer, info icon, universe transparency screen, and vitest tests | M1 Interface Contract | DONE (179 test files, 1998 tests passing) |
| M3 | Review, Adversarial Challenge & Forensic Audit | Code review, stress testing, documentation sync, forensic integrity audit | M1, M2 | DONE (APPROVED, CLEAN audit, 94 Gravity steps passed) |

## Interface Contracts

### Backend `GET /data/explain/{symbol}` ↔ Frontend `api.getExplainTicker(symbol)`
```typescript
export interface ExplainCompanyProfile {
  available: boolean;
  company_name: string | null;
  description: string | null;
  sector: string | null;
  industry: string | null;
  exchange: string | null;
  website: string | null;
  ceo: string | null;
  market_cap: number | null;
  source: string | null;
  reason: string | null;
}

export interface ExplainTracking {
  tracked: boolean;
  held: boolean;
  quantity: number | null;
  avg_cost: number | null;
  market_value: number | null;
  watchlists: string[];
  coverage_status: string;
  rating_consecutive_bad_cycles: number | null;
  rating_excluded: boolean;
  reasons: string[];
}

export interface ExplainFactorBreakdown {
  available: boolean;
  as_of: string | null;
  multifactor: Record<string, number | null> | null;
  momentum: Record<string, number | null> | null;
  volatility_regime: Record<string, number | null> | null;
  tactical: Record<string, any> | null;
  sentiment: Record<string, number | null> | null;
  raw_factors: Record<string, any>;
  reason: string | null;
}

export interface ExplainPriceHistoryStatus {
  available: boolean;
  bar_count: number;
  earliest_date: string | null;
  latest_date: string | null;
  latest_close: number | null;
  status: "ok" | "no_data" | "stale";
  reason: string | null;
}

export interface ExplainTickerResponse {
  symbol: string;
  company_profile: ExplainCompanyProfile;
  tracking: ExplainTracking;
  factor_breakdown: ExplainFactorBreakdown;
  price_history_status: ExplainPriceHistoryStatus;
}
```

## Code Layout
- Backend files:
  - `data/fmp_client.py`
  - `settings.py`
  - `shared/env_io.py`
  - `scripts/verify_fmp_profile.py`
  - `api/data_api.py`
  - `tests/test_fmp_client.py`
  - `tests/test_data_api.py`
  - `tests/test_verify_fmp_profile.py`
- Frontend files:
  - `webapp/src/api/types.ts`
  - `webapp/src/api/client.ts`
  - `webapp/src/api/mock.ts`
  - `webapp/src/context/ExplainTickerContext.tsx`
  - `webapp/src/components/ExplainTickerButton.tsx`
  - `webapp/src/components/ExplainTickerDrawer.tsx`
  - `webapp/src/screens/UniverseTransparency.tsx`
  - `webapp/src/App.tsx`
  - `webapp/src/navigation.tsx`
  - `webapp/src/components/ExplainTickerDrawer.test.tsx`
  - `webapp/src/screens/UniverseTransparency.test.tsx`
- Documentation files:
  - `CLAUDE.md`
  - `AGENTS.md`
