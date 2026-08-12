import { api } from "../api/client";
import type { ObservabilitySummary } from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading } from "./ui";
import { fmtPct } from "../format";
import { theme } from "../theme";

/**
 * Portfolio Heat widget -- live aggregate adverse open-position P&L as a
 * fraction of account equity, against the configured `MAX_PORTFOLIO_HEAT`
 * ceiling (see `PortfolioHeatMetric` in `../api/types`). Self-contained (no
 * required props) so it can be dropped into any screen, including the
 * Create Data App `/app/:slug` renderer, matching `EdgeByStrategyChart`/
 * `SymbolSignalOverlayChart`'s pattern.
 *
 * `range`/`horizon` are required params of `api.getObservabilitySummary()`,
 * but `portfolio_heat` itself is a live snapshot reading independent of
 * either -- "1M"/30 is passed only to satisfy the call signature and has no
 * bearing on what's rendered here.
 *
 * `heat_pct`/`max_portfolio_heat` are independently nullable server-side
 * (no account snapshot persisted yet, or equity missing/non-positive).
 * `heat_pct == null` is the honest cold-start case and renders the server's
 * `reason` via `EmptyState` -- NEVER a fabricated 0% (CONSTRAINT #4). A
 * present `heat_pct` with a missing `max_portfolio_heat` still renders the
 * raw reading but skips the ratio bar (an unknown ceiling has no honest
 * fill %).
 */
export function PortfolioHeatWidget() {
  const { data, loading, error, status, reload } = useApi<ObservabilitySummary>(
    () => api.getObservabilitySummary("1M", 30),
    []
  );

  if (loading) return <Loading lines={2} />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;

  const heat = data?.portfolio_heat;
  if (!heat || heat.heat_pct == null) {
    return (
      <EmptyState
        title="No portfolio heat reading yet"
        hint={heat?.reason ?? "Portfolio heat populates once an account snapshot is persisted."}
      />
    );
  }

  const hasCap = heat.max_portfolio_heat != null && heat.max_portfolio_heat > 0;
  const barPct = hasCap
    ? Math.min(100, Math.max(0, (heat.heat_pct / (heat.max_portfolio_heat as number)) * 100))
    : null;
  const overLimit = heat.over_limit === true;
  const barColor = overLimit ? theme.decline : theme.growth;

  return (
    <div data-testid="portfolioHeat-widget">
      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginBottom: "var(--s-1-5)" }}>
        Portfolio heat
      </div>
      <div
        style={{
          height: 10,
          borderRadius: 5,
          background: theme.surface2,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${barPct ?? 0}%`,
            background: barColor,
            borderRadius: 5,
          }}
        />
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          marginTop: "var(--s-1-5)",
          fontSize: "var(--t-label)",
        }}
      >
        <span
          className="num"
          style={{ color: overLimit ? theme.decline : theme.textPrimary, fontWeight: 600 }}
        >
          {fmtPct(heat.heat_pct, 1, { fromFraction: true })}
        </span>
        <span className="num" style={{ color: theme.textMuted }}>
          cap {hasCap ? fmtPct(heat.max_portfolio_heat, 0, { fromFraction: true }) : "—"}
        </span>
      </div>
      {overLimit && (
        <p style={{ color: theme.decline, fontSize: "var(--t-caption)", marginTop: "var(--s-1-5)" }}>
          Over the configured portfolio heat limit.
        </p>
      )}
      {heat.reason && (
        <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-1)" }}>
          {heat.reason}
        </p>
      )}
    </div>
  );
}
