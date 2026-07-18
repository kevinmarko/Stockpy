import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type {
  BrinsonFachlerResult,
  BrinsonFachlerRow,
  CorrelationCluster,
  FactorExposure,
  PortfolioAttribution as PortfolioAttributionT,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, EmptyState, ErrorState, Loading, StaleDataNotice, Tile } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { fmtNum, fmtPct, timeAgo } from "../format";
import { theme } from "../theme";

/** A single cluster is "heavy" when it's a real (>1 symbol) grouping making up
 * more than 30% of held market value -- a hidden-concentration warning, not a
 * hard rule. Mirrors the old Streamlit Report Viewer's cluster-concentration
 * banner threshold. */
const HEAVY_CONCENTRATION_THRESHOLD = 0.3;

const FACTOR_LABELS: Record<keyof FactorExposure, string> = {
  value_z: "Value",
  quality_z: "Quality",
  lowvol_z: "Low volatility",
  size_z: "Size",
  multifactor_composite: "Composite tilt",
};

/** Zero-centered horizontal bar for one factor's z-score, clamped to [-3, 3]
 * for display (z-scores are winsorized at +/-3 upstream anyway --
 * signals/multifactor.py). `null` renders an honest empty track, never 0. */
function FactorTiltBar({ label, value }: { label: string; value: number | null }) {
  const clamped = value == null ? 0 : Math.max(-3, Math.min(3, value));
  const halfWidthPct = value == null ? 0 : (Math.abs(clamped) / 3) * 50;
  const positive = clamped >= 0;
  const color = value == null ? theme.textMuted : positive ? theme.growth : theme.decline;

  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
        <span style={{ color: theme.textSecondary }}>{label}</span>
        <span className="num" style={{ color, fontWeight: 700 }}>
          {value == null ? "—" : fmtNum(value, 2)}
        </span>
      </div>
      <div
        style={{
          position: "relative",
          height: 8,
          borderRadius: 4,
          background: theme.surface2,
          marginTop: 4,
          overflow: "hidden",
        }}
      >
        <div
          aria-hidden
          style={{
            position: "absolute",
            left: "50%",
            top: 0,
            bottom: 0,
            width: 1,
            background: theme.borderStrong,
          }}
        />
        {value != null && (
          <div
            style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: positive ? "50%" : `${50 - halfWidthPct}%`,
              width: `${halfWidthPct}%`,
              borderRadius: 4,
              background: color,
            }}
          />
        )}
      </div>
    </div>
  );
}

function ClusterCard({ c }: { c: CorrelationCluster }) {
  return (
    <section className="card card-pad" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 15, wordBreak: "break-word" }}>
          {c.symbols.join(" + ")}
        </div>
        {c.weight_pct != null && (
          <span className="badge badge-neutral" style={{ fontWeight: 700, whiteSpace: "nowrap" }}>
            {fmtPct(c.weight_pct, 0, { fromFraction: true })} of book
          </span>
        )}
      </div>
      {c.insufficient_history ? (
        <p style={{ color: theme.textMuted, fontSize: 12.5, marginTop: 8 }}>
          Not enough price history yet to correlate{" "}
          {c.n_symbols === 1 ? "this holding" : "these holdings"}.
        </p>
      ) : (
        <div style={{ display: "flex", gap: 20, marginTop: 10 }}>
          <div>
            <div style={{ fontSize: 11, color: theme.textMuted }}>Holdings</div>
            <div className="num" style={{ fontSize: 14 }}>{c.n_symbols}</div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: theme.textMuted }}>Avg correlation</div>
            <div className="num" style={{ fontSize: 14 }}>
              {c.avg_intra_corr == null ? "—" : fmtNum(c.avg_intra_corr, 2)}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

// ===========================================================================
// Brinson-Fachler attribution — manual-input operator calculator
// ===========================================================================
// Distinct from the two auto-derived sections above (both driven by real
// holdings + the pipeline snapshot): this section's sector-level portfolio-
// vs-benchmark matrix is entirely OPERATOR-TYPED. Point-in-time sector-level
// BENCHMARK returns aren't available anywhere in this platform, so there is
// no honest way to auto-derive this -- mirrors the legacy Streamlit Command
// Center's interactive `_render_brinson_fachler_section` calculator.

const GICS_SECTORS = [
  "Energy",
  "Materials",
  "Industrials",
  "Consumer Discretionary",
  "Consumer Staples",
  "Health Care",
  "Financials",
  "Information Technology",
  "Communication Services",
  "Utilities",
  "Real Estate",
] as const;

function emptyBrinsonRows(): BrinsonFachlerRow[] {
  return GICS_SECTORS.map((sector) => ({
    sector,
    portfolio_weight_pct: 0,
    portfolio_return_pct: 0,
    benchmark_weight_pct: 0,
    benchmark_return_pct: 0,
  }));
}

/** Client-side mirror of `pilots/brinson.py::validate_brinson_fachler_rows` --
 * purely informational (never blocks Compute), so the operator sees weight-sum
 * / negative-weight issues instantly without a round-trip. The server
 * re-validates independently and returns its own authoritative
 * `validation_warnings` alongside the computed result. */
function clientSideBrinsonWarnings(rows: BrinsonFachlerRow[]): string[] {
  const warnings: string[] = [];
  const pSum = rows.reduce((a, r) => a + (r.portfolio_weight_pct || 0), 0);
  const bSum = rows.reduce((a, r) => a + (r.benchmark_weight_pct || 0), 0);
  if (Math.abs(pSum - 100) > 1) {
    warnings.push(`Portfolio weights sum to ${pSum.toFixed(2)}% (expected ~100%).`);
  }
  if (Math.abs(bSum - 100) > 1) {
    warnings.push(`Benchmark weights sum to ${bSum.toFixed(2)}% (expected ~100%).`);
  }
  if (rows.some((r) => (r.portfolio_weight_pct || 0) < 0)) {
    warnings.push("Negative values found in Portfolio Weight.");
  }
  if (rows.some((r) => (r.benchmark_weight_pct || 0) < 0)) {
    warnings.push("Negative values found in Benchmark Weight.");
  }
  return warnings;
}

function EffectTile({ label, fraction }: { label: string; fraction: number }) {
  return (
    <Tile
      label={label}
      value={fmtPct(fraction, 2, { fromFraction: true, signed: true })}
      tone={fraction >= 0 ? "pos" : "neg"}
    />
  );
}

function NumCell({
  value,
  onChange,
  ariaLabel,
}: {
  value: number;
  onChange: (v: number) => void;
  ariaLabel: string;
}) {
  return (
    <input
      type="number"
      inputMode="decimal"
      className="input"
      aria-label={ariaLabel}
      value={value}
      step={0.1}
      style={{ width: 84, padding: "6px 8px", fontSize: 13, textAlign: "right" }}
      onChange={(e) => {
        const n = Number(e.target.value);
        onChange(Number.isFinite(n) ? n : 0);
      }}
    />
  );
}

const _BF_COLUMNS: {
  field: keyof Omit<BrinsonFachlerRow, "sector">;
  header: string;
  labelSuffix: string;
}[] = [
  { field: "portfolio_weight_pct", header: "Port. weight %", labelSuffix: "portfolio weight percent" },
  { field: "portfolio_return_pct", header: "Port. return %", labelSuffix: "portfolio return percent" },
  { field: "benchmark_weight_pct", header: "Bench. weight %", labelSuffix: "benchmark weight percent" },
  { field: "benchmark_return_pct", header: "Bench. return %", labelSuffix: "benchmark return percent" },
];

function BrinsonFachlerSection() {
  const [rows, setRows] = useState<BrinsonFachlerRow[]>(emptyBrinsonRows);
  const mutation = useMutation((r: BrinsonFachlerRow[]) => api.getBrinsonFachlerAttribution(r));
  const clientWarnings = useMemo(() => clientSideBrinsonWarnings(rows), [rows]);
  const result: BrinsonFachlerResult | null = mutation.result ?? null;

  function updateCell(index: number, field: keyof Omit<BrinsonFachlerRow, "sector">, value: number) {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, [field]: value } : r)));
  }

  return (
    <>
      <h2 style={{ fontSize: 15, marginTop: 24, marginBottom: 4 }}>Brinson-Fachler attribution</h2>
      <p className="screen-sub" style={{ marginTop: 0 }}>
        Type a sector-level portfolio-vs-benchmark matrix to see how much of the active return
        came from allocation, selection, or their interaction. Manual input -- point-in-time
        sector benchmark returns aren't available to derive this automatically.
      </p>

      <section className="card card-pad" style={{ marginBottom: 16, overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${theme.borderStrong}` }}>
              <th style={{ padding: "6px 8px", textAlign: "left" }}>Sector</th>
              {_BF_COLUMNS.map((c) => (
                <th key={c.field} style={{ padding: "6px 4px", textAlign: "right", fontWeight: 600 }}>
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.sector} style={{ borderBottom: `1px solid ${theme.border}` }}>
                <td style={{ padding: "6px 8px", whiteSpace: "nowrap" }}>{row.sector}</td>
                {_BF_COLUMNS.map((c) => (
                  <td key={c.field} style={{ padding: "4px" }}>
                    <NumCell
                      ariaLabel={`${row.sector} ${c.labelSuffix}`}
                      value={row[c.field]}
                      onChange={(v) => updateCell(i, c.field, v)}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>

        {clientWarnings.length > 0 && (
          <div className="notice notice-warn" style={{ marginTop: 12 }}>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {clientWarnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </div>
        )}

        <div style={{ marginTop: 14 }}>
          <Button variant="primary" pending={mutation.pending} onClick={() => mutation.run(rows)}>
            Compute
          </Button>
        </div>

        {mutation.error && (
          <div className="notice notice-warn" style={{ marginTop: 12 }} data-testid="brinson-error">
            <span>{mutation.error}</span>
          </div>
        )}
      </section>

      {result && (
        <>
          <div className="tiles" style={{ marginBottom: 16 }}>
            <EffectTile label="Portfolio return" fraction={result["Portfolio Return"]} />
            <EffectTile label="Benchmark return" fraction={result["Benchmark Return"]} />
            <EffectTile label="Active return" fraction={result["Active Return"]} />
            <EffectTile label="Allocation effect" fraction={result["Allocation Effect"]} />
            <EffectTile label="Selection effect" fraction={result["Selection Effect"]} />
            <EffectTile label="Interaction effect" fraction={result["Interaction Effect"]} />
          </div>

          {result.validation_warnings.length > 0 && (
            <div className="notice notice-info" style={{ marginBottom: 12 }}>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {result.validation_warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          <section className="card card-pad" style={{ marginBottom: 16, overflowX: "auto" }}>
            <h3 style={{ fontSize: 14, margin: "0 0 8px" }}>Per-sector effects</h3>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.borderStrong}` }}>
                  <th style={{ padding: "6px 8px", textAlign: "left" }}>Sector</th>
                  <th style={{ padding: "6px 8px", textAlign: "right" }}>Allocation</th>
                  <th style={{ padding: "6px 8px", textAlign: "right" }}>Selection</th>
                  <th style={{ padding: "6px 8px", textAlign: "right" }}>Interaction</th>
                  <th style={{ padding: "6px 8px", textAlign: "right" }}>Total</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(result["Sector Details"]).map(([sector, d]) => (
                  <tr key={sector} style={{ borderBottom: `1px solid ${theme.border}` }}>
                    <td style={{ padding: "6px 8px" }}>{sector}</td>
                    <td
                      className="num"
                      style={{
                        padding: "6px 8px",
                        textAlign: "right",
                        color: d.allocation_effect >= 0 ? theme.growth : theme.decline,
                      }}
                    >
                      {fmtPct(d.allocation_effect, 2, { fromFraction: true, signed: true })}
                    </td>
                    <td
                      className="num"
                      style={{
                        padding: "6px 8px",
                        textAlign: "right",
                        color: d.selection_effect >= 0 ? theme.growth : theme.decline,
                      }}
                    >
                      {fmtPct(d.selection_effect, 2, { fromFraction: true, signed: true })}
                    </td>
                    <td
                      className="num"
                      style={{
                        padding: "6px 8px",
                        textAlign: "right",
                        color: d.interaction_effect >= 0 ? theme.growth : theme.decline,
                      }}
                    >
                      {fmtPct(d.interaction_effect, 2, { fromFraction: true, signed: true })}
                    </td>
                    <td
                      className="num"
                      style={{
                        padding: "6px 8px",
                        textAlign: "right",
                        fontWeight: 700,
                        color: d.total_attribution >= 0 ? theme.growth : theme.decline,
                      }}
                    >
                      {fmtPct(d.total_attribution, 2, { fromFraction: true, signed: true })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </>
  );
}

function AttributionBody({ data }: { data: PortfolioAttributionT }) {
  const fe = data.factor_exposure;
  const cc = data.correlation_clusters;
  const heavy = cc.clusters.filter(
    (c) => !c.insufficient_history && (c.weight_pct ?? 0) > HEAVY_CONCENTRATION_THRESHOLD
  );

  return (
    <>
      <h2 style={{ fontSize: 15, marginTop: 8, marginBottom: 8 }}>Factor exposure</h2>
      {fe.coverage.held_count === 0 ? (
        <EmptyState
          title="No holdings yet"
          hint="Connect a brokerage or run the pipeline to see your factor tilts."
        />
      ) : fe.coverage.matched_count === 0 ? (
        <EmptyState
          title="No factor data yet"
          hint={fe.reason ?? "Run the pipeline to score your holdings."}
        />
      ) : (
        <section className="card card-pad" style={{ marginBottom: 16 }}>
          {(Object.keys(FACTOR_LABELS) as (keyof FactorExposure)[]).map((key) => (
            <FactorTiltBar key={key} label={FACTOR_LABELS[key]} value={fe.exposures[key]} />
          ))}
          <p style={{ color: theme.textMuted, fontSize: 11.5, marginTop: 8 }}>
            Covers {fmtPct(fe.coverage.matched_value_pct, 0, { fromFraction: true })} of your
            book ({fe.coverage.matched_count} of {fe.coverage.held_count} holdings scored).
            {fe.coverage.unmatched_symbols.length > 0 && (
              <> Not yet scored: {fe.coverage.unmatched_symbols.join(", ")}.</>
            )}
          </p>
          {fe.as_of && (
            <p style={{ color: theme.textMuted, fontSize: 11, marginTop: 4 }}>
              As of {timeAgo(fe.as_of)}
            </p>
          )}
        </section>
      )}

      <h2 style={{ fontSize: 15, marginBottom: 4 }}>Correlation clusters</h2>
      <p className="screen-sub" style={{ marginTop: 0 }}>
        Holdings that tend to move together, over the last {cc.lookback_days} trading days.
      </p>

      {heavy.length > 0 && (
        <div className="notice notice-warn" style={{ marginBottom: 12 }}>
          <span>
            High concentration: {heavy.map((c) => c.symbols.join("+")).join(", ")} move together
            and make up a large share of your book. Consider diversifying.
          </span>
        </div>
      )}

      {cc.clusters.length === 0 ? (
        <EmptyState
          title="No clusters yet"
          hint={cc.reason ?? "Not enough price history to correlate your holdings."}
        />
      ) : (
        cc.clusters.map((c) => <ClusterCard key={c.cluster_id} c={c} />)
      )}
    </>
  );
}

export function Attribution() {
  const nav = useNavigate();
  const { data, loading, error, status, stale, cachedAt, reload } =
    useApi<PortfolioAttributionT>(() => api.getPortfolioAttribution(), []);
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: theme.textSecondary,
          fontSize: 14,
          marginBottom: 8,
        }}
      >
        ← Pilots
      </button>
      <h1 className="screen-title">Portfolio attribution</h1>
      <p className="screen-sub">
        What factor tilts and hidden concentration your actual holdings carry
        -- a read of your current book, not a backtest.
      </p>

      <TabGuide tabKey="attribution" />

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        <>
          {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}
          <AttributionBody data={data} />
        </>
      )}

      {/* Manual-input calculator -- independent of the holdings-derived
          sections above; renders regardless of their load/error state. */}
      <BrinsonFachlerSection />
    </div>
  );
}
