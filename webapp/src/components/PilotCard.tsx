import { Link } from "react-router-dom";
import type { PilotSummary } from "../api/types";
import { CategoryChip, DeployableBadge } from "./ui";
import { fmtNum, fmtPct } from "../format";
import { theme } from "../theme";

/**
 * Marketplace rail card. Performance-percentage-forward: the headline metric
 * (Sharpe) leads. A non-deployable Pilot shows its badge plainly.
 */
export function PilotCard({ pilot }: { pilot: PilotSummary }) {
  const h = pilot.headline;
  const sharpe = h.sharpe;
  return (
    <Link
      to={`/pilots/${pilot.id}`}
      className="card"
      style={{
        display: "block",
        width: 220,
        padding: 14,
        textDecoration: "none",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 6,
        }}
      >
        <CategoryChip category={pilot.category} />
        {pilot.long_only && <span className="chip">Long-only</span>}
      </div>

      <div style={{ fontSize: 17, fontWeight: 700, marginTop: 10 }}>
        {pilot.name}
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 8,
          marginTop: 10,
        }}
      >
        <span
          className="num"
          style={{
            fontSize: 26,
            fontWeight: 800,
            color: sharpe == null ? theme.textMuted : theme.growth,
            letterSpacing: "-0.02em",
          }}
        >
          {sharpe == null ? "—" : fmtNum(sharpe, 2)}
        </span>
        <span style={{ fontSize: 12, color: theme.textMuted }}>Sharpe</span>
      </div>

      <div
        style={{
          fontSize: 12,
          color: theme.textSecondary,
          marginTop: 2,
          display: "flex",
          gap: 10,
        }}
      >
        <span>
          Max DD{" "}
          {h.max_drawdown == null
            ? "—"
            : fmtPct(h.max_drawdown, 0, { fromFraction: true })}
        </span>
        <span>· {pilot.holdings_count} holdings</span>
      </div>

      <div style={{ marginTop: 12 }}>
        <DeployableBadge deployable={h.deployable} />
      </div>
    </Link>
  );
}

/** Compact popularity card (Most Popular rail). */
export function PopularCard({ pilot }: { pilot: PilotSummary }) {
  return (
    <Link
      to={`/pilots/${pilot.id}`}
      className="card"
      style={{ display: "block", width: 200, padding: 14 }}
    >
      <div style={{ fontSize: 16, fontWeight: 700 }}>{pilot.name}</div>
      <div style={{ fontSize: 12, color: theme.textMuted, marginTop: 2 }}>
        {pilot.category}
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          marginTop: 14,
        }}
      >
        <div>
          <div className="tile-label">Followers</div>
          <div className="num" style={{ fontWeight: 700, fontSize: 18 }}>
            {pilot.followers_proxy.toLocaleString()}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="tile-label">AUM (proxy)</div>
          <div className="num" style={{ fontWeight: 700, fontSize: 18 }}>
            {new Intl.NumberFormat("en-US", {
              style: "currency",
              currency: "USD",
              notation: "compact",
              maximumFractionDigits: 1,
            }).format(pilot.aum_proxy)}
          </div>
        </div>
      </div>
    </Link>
  );
}
