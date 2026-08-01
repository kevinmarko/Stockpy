import { Sparkline } from "./charts";
import { Tile } from "./ui";
import { fmtNum, fmtPct } from "../format";

interface TickerDrawerProps {
  symbol: string;
  onClose: () => void;
}

export function TickerDrawer({ symbol, onClose }: TickerDrawerProps) {
  // Mock data for ticker inspection
  const notionalValue = 12450;
  const equityCap = 100000;
  const weight = notionalValue / equityCap;

  const mockSparkData = [
    { date: "1", value: 142 },
    { date: "2", value: 145 },
    { date: "3", value: 143 },
    { date: "4", value: 148 },
    { date: "5", value: 152 },
    { date: "6", value: 150 },
    { date: "7", value: 155 },
    { date: "8", value: 158 },
    { date: "9", value: 154 },
    { date: "10", value: 160 },
  ];

  const signals = [
    { name: "CrossSectionalMomentum", score: 0.78, weight: "40%" },
    { name: "MultifactorSignal", score: 0.42, weight: "30%" },
    { name: "NewsCatalystSignal", score: -0.12, weight: "30%" },
  ];

  const rejections = [
    { time: "14:22:01", reason: "max_order_rate", details: "Rate limit 5 orders/min exceeded" },
    { time: "09:31:15", reason: "market_hours", details: "Order attempted outside core RTH window" },
  ];

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: "min(100vw, 480px)",
        background: "var(--surface)",
        borderLeft: "1px solid var(--border)",
        boxShadow: "-10px 0 30px rgba(0, 0, 0, 0.5)",
        zIndex: 9000,
        display: "flex",
        flexDirection: "column",
        animation: "slideLeft 0.2s ease-out",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "var(--s-4)",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: "var(--surface-2)",
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
            <h2 style={{ margin: 0, fontSize: "var(--t-title)", fontWeight: 700, color: "var(--text-primary)" }}>
              {symbol}
            </h2>
            <span style={{ fontSize: "var(--t-caption)", padding: "2px 6px", borderRadius: "var(--r-xs)", background: "var(--surface-3)", color: "var(--accent)" }}>
              EQUITY
            </span>
          </div>
          <div style={{ color: "var(--text-muted)", fontSize: "var(--t-caption)", marginTop: "2px" }}>
            Inspection & Signal Breakdown
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            background: "none",
            border: "none",
            color: "var(--text-muted)",
            fontSize: "24px",
            cursor: "pointer",
            padding: "0 var(--s-2)",
          }}
        >
          ×
        </button>
      </div>

      {/* Content body */}
      <div style={{ flex: 1, overflowY: "auto", padding: "var(--s-4)", display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
        {/* Quick chart */}
        <div className="card card-pad" style={{ background: "var(--surface-2)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "var(--s-2)" }}>
            <span style={{ fontSize: "var(--t-subhead)", fontWeight: 600 }}>Trend Preview</span>
            <span style={{ color: "var(--growth)", fontSize: "var(--t-callout)", fontWeight: 700 }}>+4.2%</span>
          </div>
          <Sparkline data={mockSparkData} positive={true} />
        </div>

        {/* Portfolio Sizing */}
        <div>
          <h3 style={{ fontSize: "var(--t-callout)", margin: "0 0 var(--s-2)", color: "var(--text-primary)" }}>
            Portfolio Sizing & Cap
          </h3>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--s-2)" }}>
            <Tile label="Notional Value" value={`$${fmtNum(notionalValue, 0)}`} />
            <Tile label="Portfolio Weight" value={fmtPct(weight, 1, { fromFraction: true })} />
          </div>
          <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", marginTop: "var(--s-1-5)" }}>
            Single-stock cap limit: $100,000 (100% of equity cap)
          </div>
        </div>

        {/* Signal Score Breakdown */}
        <div>
          <h3 style={{ fontSize: "var(--t-callout)", margin: "0 0 var(--s-2)", color: "var(--text-primary)" }}>
            Signal Score Breakdown
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
            {signals.map((sig) => (
              <div
                key={sig.name}
                style={{
                  background: "var(--surface-2)",
                  padding: "var(--s-2-5) var(--s-3)",
                  borderRadius: "var(--r-xs)",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <div style={{ fontSize: "var(--t-callout)", fontWeight: 600 }}>{sig.name}</div>
                  <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)" }}>Weight: {sig.weight}</div>
                </div>
                <span
                  style={{
                    fontWeight: 700,
                    fontSize: "var(--t-callout)",
                    color: sig.score > 0 ? "var(--growth)" : sig.score < 0 ? "var(--decline)" : "var(--text-muted)",
                  }}
                >
                  {sig.score > 0 ? `+${sig.score}` : sig.score}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Recent Block / Rejection History */}
        <div>
          <h3 style={{ fontSize: "var(--t-callout)", margin: "0 0 var(--s-2)", color: "var(--text-primary)" }}>
            Recent Risk Block History
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
            {rejections.map((rej, i) => (
              <div
                key={i}
                style={{
                  background: "var(--surface-2)",
                  borderLeft: "3px solid var(--caution)",
                  padding: "var(--s-2-5) var(--s-3)",
                  borderRadius: "0 var(--r-xs) var(--r-xs) 0",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--t-caption)" }}>
                  <span style={{ fontWeight: 700, color: "var(--caution)" }}>{rej.reason}</span>
                  <span style={{ color: "var(--text-muted)" }}>{rej.time}</span>
                </div>
                <div style={{ fontSize: "var(--t-caption)", color: "var(--text-secondary)", marginTop: "2px" }}>
                  {rej.details}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
