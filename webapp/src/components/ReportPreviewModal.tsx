import { Modal } from "./Modal";

interface ReportPreviewModalProps {
  title: string;
  onClose: () => void;
}

export function ReportPreviewModal({ title, onClose }: ReportPreviewModalProps) {
  return (
    <Modal ariaLabel={title} onClose={onClose}>
      <div style={{ width: "min(90vw, 720px)", maxHeight: "70vh", overflowY: "auto", padding: "var(--s-2)" }}>
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: "var(--r-md)",
            padding: "var(--s-4)",
            fontSize: "var(--t-body)",
            lineHeight: 1.6,
            color: "var(--text-primary)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "var(--s-3)", borderBottom: "1px solid var(--border)", paddingBottom: "var(--s-2)" }}>
            <span style={{ fontWeight: 700, fontSize: "var(--t-subhead)" }}>InvestYo Executive Briefing</span>
            <span style={{ color: "var(--text-muted)", fontSize: "var(--t-caption)" }}>2026-08-01 16:00 ET</span>
          </div>

          <h3 style={{ fontSize: "var(--t-callout)", color: "var(--growth)", marginTop: 0 }}>System Health & Portfolio Status</h3>
          <p>
            The portfolio regime remains classified as <strong>RISK-ON</strong> with Sahm Rule indicator at 0.20% and VIX resting at 15.40. No macro kill switch triggers were encountered during today's advisory pass.
          </p>

          <h3 style={{ fontSize: "var(--t-callout)", color: "var(--accent)" }}>Top Strategy Signals</h3>
          <ul>
            <li><strong>NVDA</strong>: CrossSectionalMomentum score <code>+0.82</code> (Kelly Sizing 3.2% allocated).</li>
            <li><strong>AAPL</strong>: Multifactor low-volatility score <code>+0.45</code> (Target allocation met).</li>
            <li><strong>SPY</strong>: Regulated index overlay active (HMM probability 88% low-vol regime).</li>
          </ul>

          <h3 style={{ fontSize: "var(--t-callout)", color: "var(--caution)" }}>Risk Gate Audit Highlights</h3>
          <p style={{ color: "var(--text-secondary)", fontSize: "var(--t-caption)" }}>
            A total of 4 orders were rejected by <code>max_order_rate</code> protection rules during the pre-market window. All position sizes remain comfortably under the single-stock equity cap.
          </p>
        </div>
      </div>
    </Modal>
  );
}
