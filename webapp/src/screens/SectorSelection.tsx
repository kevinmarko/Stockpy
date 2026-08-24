import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { SectorSelectionRow, SectorSelectionView } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Input, InfoTip, Loading, Notice, StaleDataNotice, Table } from "../components/ui";
import { SymbolInput } from "../components/SymbolInput";
import { TabGuide } from "../components/TabGuide";
import { fmtDate, fmtNum, fmtPct } from "../format";
import { theme } from "../theme";

const MIN_N = 1;
const MAX_N = 5;
const DEFAULT_TARGET = "AAPL";

/** Short, plain-English gloss for each degraded_reason tag -- the row-level
 * counterpart to the persistent banner below (which only fires for the
 * common review_unavailable case shared across every computed row). */
function degradedReasonLabel(reason: string): string {
  switch (reason) {
    case "no_embedder":
      return "No similarity backend configured";
    case "no_target_description":
      return "No description for the target symbol";
    case "no_sector_description":
      return "No description for this sector";
    case "embedding_failed":
      return "Embedding failed";
    case "review_unavailable":
      return "Investor-forum volume unavailable — news-only";
    case "no_volume_observed":
      return "No sentiment volume observed for this sector";
    default:
      return reason;
  }
}

function NumCell({ value, digits = 3 }: { value: number | null | undefined; digits?: number }) {
  return (
    <td className="num">
      {value == null ? <span className="muted">—</span> : fmtNum(value, digits)}
    </td>
  );
}

/**
 * Dated FMP sector 1-day-change cell — colored using this codebase's
 * established growth/decline convention (see the `correlation_coefficient`
 * cell just below, and `theme.growth`/`theme.decline` elsewhere). `null`/
 * `undefined` (no snapshot for this sector) renders an honest dash, never a
 * fabricated 0%.
 */
function ChangePctCell({ value }: { value: number | null | undefined }) {
  return (
    <td
      className="num"
      style={{
        color: value == null ? undefined : value >= 0 ? theme.growth : theme.decline,
      }}
    >
      {value == null ? <span className="muted">—</span> : fmtPct(value, 2, { fromFraction: true, signed: true })}
    </td>
  );
}

function SectorRow({ row }: { row: SectorSelectionRow }) {
  return (
    <tr>
      <td>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
          {row.selected && (
            <InfoTip triggerClassName="badge badge-good" content="In the top-N selection">
              ✓
            </InfoTip>
          )}
          <span style={{ fontWeight: row.selected ? 700 : 500 }}>{row.sector}</span>
        </div>
        {row.degraded_reason && (
          <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginTop: "var(--s-0-5)" }}>
            {degradedReasonLabel(row.degraded_reason)}
          </div>
        )}
      </td>
      <NumCell value={row.cosine_similarity} />
      <NumCell value={row.ingestion_volume} digits={1} />
      <NumCell value={row.sector_heat_factor} />
      <td
        className="num"
        style={{
          fontWeight: 700,
          color:
            row.correlation_coefficient == null
              ? theme.textSecondary
              : row.correlation_coefficient >= 0
                ? theme.growth
                : theme.decline,
        }}
      >
        {row.correlation_coefficient == null ? (
          <span className="muted">—</span>
        ) : (
          fmtNum(row.correlation_coefficient, 4)
        )}
      </td>
      <td className="num">
        {row.rank == null ? <span className="muted">—</span> : `#${row.rank}`}
      </td>
      <NumCell value={row.pe} digits={1} />
      <ChangePctCell value={row.change_pct} />
    </tr>
  );
}

export function SectorSelection() {
  const nav = useNavigate();
  const [target, setTarget] = useState(DEFAULT_TARGET);
  const [n, setN] = useState(3);

  const { data, loading, error, status, stale, cachedAt, reload } = useApi<SectorSelectionView>(
    () => api.getSectorSelection(target, n),
    [target, n]
  );

  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  const rows = data?.rows ?? [];
  const anyReviewUnavailable = useMemo(
    () => rows.some((r) => r.degraded_reason === "review_unavailable"),
    [rows]
  );
  const selectedCount = rows.filter((r) => r.selected).length;

  const nInvalid = !Number.isInteger(n) || n < MIN_N || n > MAX_N;

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
          fontSize: "var(--t-callout)",
          marginBottom: "var(--s-2)",
        }}
      >
        ← Back
      </button>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "var(--s-2)" }}>
        <div>
          <h1 className="screen-title">Sector Selection</h1>
          {data?.as_of && (
            <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>{fmtDate(data.as_of)}</span>
          )}
        </div>
        <button
          onClick={() => nav("/settings/sector-selection")}
          style={{
            padding: "6px 12px",
            borderRadius: "var(--r-sm)",
            background: "transparent",
            border: `1px solid ${theme.border}`,
            color: theme.textSecondary,
            fontSize: "var(--t-caption)",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Configure sector selection →
        </button>
      </div>

      <TabGuide tabKey="sector-selection" />

      <SymbolInput
        initial={target}
        label="Target symbol"
        onSubmit={(sym) => setTarget(sym)}
        pending={loading}
        // GET /sector/selection only ever reads persisted DB state -- an
        // FMP-known but untracked symbol is a guaranteed honest-empty dead
        // end here, so suggesting one would just be misleading.
        enableFmpSuggestions={false}
      />

      <div style={{ maxWidth: 160, marginBottom: "var(--s-4)" }}>
        <Input
          label={`Related sectors to select (${MIN_N}-${MAX_N})`}
          type="number"
          min={MIN_N}
          max={MAX_N}
          value={n}
          invalid={nInvalid}
          onChange={(e) => {
            const parsed = Number(e.target.value);
            setN(Number.isFinite(parsed) ? Math.trunc(parsed) : n);
          }}
          hint={nInvalid ? `Must be between ${MIN_N} and ${MAX_N}.` : undefined}
        />
      </div>

      {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}

      {!loading && !error && data && rows.length === 0 && (
        <div className="empty" style={{ padding: "var(--s-7-5)" }}>
          {data.reason ?? "No sector selection has been computed for this symbol yet."}
        </div>
      )}

      {!loading && !error && data && rows.length > 0 && (
        <>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--s-2)",
              margin: "var(--s-2) 0 var(--s-3)",
              fontSize: "var(--t-label)",
              color: theme.textSecondary,
            }}
          >
            <span className="chip">
              {selectedCount} of {rows.length} selected
            </span>
            {data.embedder && (
              <span className="chip">
                {data.embedder}
                {data.pooling ? ` · ${data.pooling}-pooled` : ""}
              </span>
            )}
          </div>

          {anyReviewUnavailable && (
            <Notice variant="warn" style={{ marginBottom: "var(--s-3)" }} data-testid="review-unavailable-banner">
              <span>
                Investor-forum comment volume is not being ingested, so the Review term is
                unavailable. Sector Heat is computed from news volume only.
              </span>
            </Notice>
          )}

          <section className="card card-pad" style={{ overflowX: "auto" }}>
            <Table>
              <thead>
                <tr>
                  <th>Sector</th>
                  <th className="num">Cosine sim.</th>
                  <th className="num">Ingestion vol.</th>
                  <th className="num">Heat (SHF)</th>
                  <th className="num">Coefficient</th>
                  <th className="num">Rank</th>
                  <th className="num">P/E</th>
                  <th className="num">1D Chg</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <SectorRow key={row.sector} row={row} />
                ))}
              </tbody>
            </Table>
          </section>

          <p style={{ fontSize: "var(--t-footnote)", color: theme.textMuted, marginTop: "var(--s-2-5)", lineHeight: 1.5 }}>
            Ranked by <code>correlation_coefficient = cosine_similarity × Sector Heat Factor</code>,
            over a trailing 22-trading-day window. Advisory research only — this does not feed any
            order or sizing decision.
          </p>
        </>
      )}
    </div>
  );
}
