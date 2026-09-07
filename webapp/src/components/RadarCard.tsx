import { Link } from "react-router";
import type { RadarItem } from "../api/types";
import { fmtNum, fmtUsd } from "../format";
import { theme } from "../theme";

/**
 * One "Today's Radar" card (`GET /signals/radar`, `pilots/radar_ranking.py`).
 * `reason` is rendered VERBATIM — it is server-templated from real,
 * already-persisted fields only (never re-derived or paraphrased
 * client-side; see the backend's own docstring for the honesty contract).
 * Clicking navigates into the existing Signal Breakdown screen for the full
 * per-module drill-down, mirroring the "Explore" tiles' click-through
 * pattern elsewhere on this screen.
 */
export function RadarCard({ item }: { item: RadarItem }) {
  return (
    <Link
      to={`/signals?symbol=${encodeURIComponent(item.symbol)}`}
      className="card card-pad"
      style={{ textDecoration: "none", display: "block", minWidth: 220 }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontWeight: 700, fontSize: "var(--t-title)", color: theme.textPrimary }}>
          {item.symbol}
        </span>
        <span
          style={{
            fontSize: "var(--t-micro)",
            fontWeight: 700,
            color: theme.accent,
            background: "rgba(99,102,241,0.12)",
            padding: "1px 6px",
            borderRadius: "var(--r-2xs)",
          }}
        >
          #{item.rank}
        </span>
      </div>
      <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-1)", flexWrap: "wrap" }}>
        {item.sector && (
          <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>{item.sector}</span>
        )}
        {item.price != null && (
          <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
            {fmtUsd(item.price)}
          </span>
        )}
        <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
          Composite {fmtNum(item.multifactor_composite, 2)}
        </span>
      </div>
      <p
        style={{
          marginTop: "var(--s-1-5)",
          fontSize: "var(--t-footnote)",
          color: theme.textSecondary,
          lineHeight: 1.4,
        }}
      >
        {item.reason}
      </p>
    </Link>
  );
}
