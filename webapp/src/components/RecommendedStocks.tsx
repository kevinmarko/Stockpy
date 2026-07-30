import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import type { ExecutionQueue, Recommendation, RecommendationsResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading } from "./ui";
import { fmtNum, fmtPct, timeAgo } from "../format";
import { theme } from "../theme";

/**
 * "Recommended stocks" — the platform's current BUY picks from the latest
 * advisory snapshot, ranked by conviction (then score). Shared by the Data
 * Explorer (which passes `onSelect` to load a pick into its bars/fundamentals
 * view) and the Compare screen (no `onSelect` → navigates to the pick's detail
 * page).
 *
 * Every row also carries two ALWAYS-present action affordances, independent of
 * `onSelect`, so viewing a recommendation and acting on it are never two
 * disconnected screens: a "Detail" link into `/symbol/:ticker` (which surfaces
 * Held-by-Pilots → Follow, and the Decision journal), and an "In queue" badge
 * — cross-referenced against `GET /execution-queue` — linking to Agentic
 * Trading when the backend's execution-queue builder has already turned this
 * same recommendation into a pending order intent.
 *
 * Honesty (CONSTRAINT #4): a `null` conviction/score/price/buy_range renders
 * "—", never a fabricated 0. Empty (cold start) renders the API's honest
 * `reason`, not a fake row. A failed/empty execution-queue fetch degrades to
 * "nothing queued" rather than blocking the recommendations themselves.
 */
export function RecommendedStocks({
  onSelect,
  limit = 25,
}: {
  onSelect?: (symbol: string) => void;
  limit?: number;
}) {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<RecommendationsResponse>(
    () => api.getRecommendations(limit),
    [limit]
  );
  const queue = useApi<ExecutionQueue>(() => api.getExecutionQueue(), []);
  const queuedSymbols = new Set(
    (queue.data?.intents ?? []).map((i) => i.symbol.toUpperCase())
  );

  const select = (symbol: string) => {
    if (onSelect) onSelect(symbol);
    else nav(`/symbol/${encodeURIComponent(symbol)}`);
  };

  return (
    <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }} data-testid="recommended-stocks">
      <h2 style={{ fontSize: "var(--t-subhead)", margin: "0 0 var(--s-1)" }}>Recommended stocks</h2>
      <p style={{ margin: "0 0 var(--s-2-5)", fontSize: "var(--t-body)", color: theme.textMuted }}>
        The platform's current BUY picks, ranked by conviction. From the latest pipeline run
        {data && ` (${timeAgo(data.as_of)})`}.
      </p>

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && data.recommendations.length === 0 && (
        <EmptyState
          title="No recommendations yet"
          hint={data.reason ?? "Run the pipeline to generate BUY signals."}
        />
      )}
      {!loading && !error && data && data.recommendations.length > 0 && (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {data.recommendations.map((r) => (
            <RecRow
              key={r.symbol}
              r={r}
              onSelect={select}
              queued={queuedSymbols.has(r.symbol.toUpperCase())}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function RecRow({
  r,
  onSelect,
  queued,
}: {
  r: Recommendation;
  onSelect: (s: string) => void;
  queued: boolean;
}) {
  return (
    <li style={{ borderTop: `1px solid ${theme.border}` }}>
      <div style={{ display: "flex", alignItems: "stretch", gap: "var(--s-1)" }}>
        <button
          type="button"
          onClick={() => onSelect(r.symbol)}
          data-testid={`rec-row-${r.symbol}`}
          style={{
            display: "flex",
            flex: 1,
            minWidth: 0,
            alignItems: "center",
            gap: "var(--s-3)",
            padding: "var(--s-2-5) var(--s-1)",
            background: "none",
            border: "none",
            cursor: "pointer",
            textAlign: "left",
            color: "inherit",
          }}
        >
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
              <span style={{ fontWeight: 700, color: theme.textPrimary }}>{r.symbol}</span>
              {r.action && (
                <span
                  style={{
                    fontSize: "var(--t-micro)",
                    fontWeight: 700,
                    color: theme.growth,
                    background: "rgba(16,185,129,0.12)",
                    padding: "1px 6px",
                    borderRadius: "var(--r-2xs)",
                    whiteSpace: "nowrap",
                  }}
                >
                  {r.action}
                </span>
              )}
            </div>
            <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted, marginTop: "var(--s-0-5)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {[r.sector, r.buy_range].filter(Boolean).join(" · ") || "—"}
            </div>
          </div>
          <div style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
            <div style={{ fontWeight: 700, color: theme.accent }}>
              {fmtPct(r.conviction, 0, { fromFraction: true })}
            </div>
            <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>
              score {fmtNum(r.score, 1)}
            </div>
          </div>
        </button>

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-end",
            justifyContent: "center",
            gap: "var(--s-1)",
            paddingRight: 4,
            flexShrink: 0,
          }}
        >
          {queued && (
            <Link
              to="/agentic"
              data-testid={`rec-queued-${r.symbol}`}
              style={{
                fontSize: "var(--t-micro)",
                fontWeight: 700,
                color: theme.accent,
                background: "rgba(56,189,248,0.12)",
                padding: "1px 6px",
                borderRadius: "var(--r-2xs)",
                whiteSpace: "nowrap",
                textDecoration: "none",
              }}
            >
              In queue
            </Link>
          )}
          <Link
            to={`/symbol/${encodeURIComponent(r.symbol)}`}
            data-testid={`rec-detail-${r.symbol}`}
            style={{
              fontSize: "var(--t-caption)",
              color: theme.textMuted,
              whiteSpace: "nowrap",
              textDecoration: "none",
            }}
          >
            Detail →
          </Link>
        </div>
      </div>
    </li>
  );
}
