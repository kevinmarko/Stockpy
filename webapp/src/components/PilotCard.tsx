import { Link } from "react-router";
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
      className="card pilot-card"
      style={{ textDecoration: "none" }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: "var(--s-1-5)",
        }}
      >
        <CategoryChip category={pilot.category} />
        {pilot.long_only && <span className="chip">Long-only</span>}
      </div>

      <div style={{ fontSize: 17, fontWeight: 700, marginTop: "var(--s-2-5)" }}>
        {pilot.name}
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: "var(--s-2)",
          marginTop: "var(--s-2-5)",
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
        <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>Sharpe</span>
      </div>

      <div
        style={{
          fontSize: "var(--t-caption)",
          color: theme.textSecondary,
          marginTop: "var(--s-0-5)",
          display: "flex",
          gap: "var(--s-2-5)",
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

      <div style={{ marginTop: "var(--s-3)" }}>
        {/* interactive=false: this badge is nested inside the card's own
            <Link>, and an InfoTip's own tap trigger would be a second
            focusable/clickable element inside an <a> -- invalid HTML and
            unreliable for keyboard/screen-reader users. The explanation is
            one tap away anyway: tapping the card navigates to Pilot Detail,
            which renders this same badge outside any link (via HonestyRow),
            where it IS interactive. */}
        <DeployableBadge deployable={h.deployable} interactive={false} />
      </div>
    </Link>
  );
}

/** Compact popularity card (Most Popular rail). */
export function PopularCard({ pilot }: { pilot: PilotSummary }) {
  return (
    <Link to={`/pilots/${pilot.id}`} className="card popular-card">
      <div style={{ fontSize: "var(--t-input)", fontWeight: 700 }}>{pilot.name}</div>
      <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted, marginTop: "var(--s-0-5)" }}>
        {pilot.category}
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          marginTop: "var(--s-3-5)",
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
