import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { Portfolio, Follow } from "../api/types";
import { useApi } from "../hooks/useApi";
import { theme } from "../theme";

/**
 * NotebookMLExport — copies/downloads the current portfolio + active follows as
 * a JSON payload for downstream LLM ("NotebookML") analysis.
 *
 * HONESTY (CONSTRAINT #4 — never fabricate data): every money field serializes
 * as JSON `null` when the underlying value is absent — NEVER a fabricated `0`.
 * A `0` fed to an LLM reads as a real balance/holding, so `?? 0` coercion is
 * banned here. The export is also gated on a resolved portfolio: the Copy /
 * Download buttons stay disabled and `timestamp` stays `null` until real data
 * has loaded, so an all-null payload can never be exported mid-fetch stamped
 * with a real wall-clock time.
 */
export function NotebookMLExport({
  portfolio,
}: { portfolio?: Portfolio | null } = {}) {
  const [copySuccess, setCopySuccess] = useState(false);
  const [fallbackWarning, setFallbackWarning] = useState("");

  // When a `portfolio` prop is supplied (even `null`, e.g. a parent still
  // loading), this component is prop-driven and does NOT fetch. Only the
  // stand-alone usage (`<NotebookMLExport />`) fetches its own snapshot.
  const hasProp = portfolio !== undefined;
  const portApi = useApi<Portfolio>(
    () => (hasProp ? Promise.resolve(portfolio as Portfolio) : api.getPortfolio()),
    [portfolio]
  );
  const portData: Portfolio | null = hasProp ? portfolio ?? null : portApi.data;
  const follows = useApi<Follow[]>(() => api.getFollows(), []);

  // "Ready" == we hold a resolved, non-null portfolio. Until then, no export.
  const ready = portData != null;

  const payload = useMemo(() => {
    const positions = Array.isArray(portData?.positions) ? portData!.positions : [];

    let layoutMetadata: unknown = null;
    try {
      const savedLayout = localStorage.getItem("dashboard_layout");
      if (savedLayout) layoutMetadata = JSON.parse(savedLayout);
    } catch {
      /* corrupt/absent layout — omit rather than fabricate */
    }

    const followed_pilots = (follows.data ?? []).map((f) => ({
      pilot_id: f.pilot_id,
      amount: f.amount,
      status: f.status,
    }));

    return {
      // `null` (never a fabricated wall-clock stamp) until the portfolio resolves.
      timestamp: ready ? new Date().toISOString() : null,
      portfolio: {
        // `?? null` normalizes undefined→null; a genuine `0` (real balance) is
        // preserved. NEVER `?? 0` — that would invent a balance (CONSTRAINT #4).
        total_equity: portData?.total_equity ?? null,
        buying_power: portData?.buying_power ?? null,
        positions: positions.map((p) => ({
          symbol: p.symbol,
          qty: p.qty ?? null,
          avg_cost: p.avg_cost ?? null,
          market_value: p.market_value ?? null,
          name: p.name ?? null,
        })),
      },
      followed_pilots,
      dashboard_layout: layoutMetadata,
    };
  }, [portData, follows.data, ready]);

  const handleCopy = async () => {
    if (!ready) return;
    const jsonStr = JSON.stringify(payload, null, 2);

    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      setFallbackWarning("Clipboard API not available. Please select and copy text manually.");
      return;
    }

    try {
      await navigator.clipboard.writeText(jsonStr);
      setCopySuccess(true);
      setFallbackWarning("");
      setTimeout(() => setCopySuccess(false), 2000);
    } catch {
      setFallbackWarning("Failed to copy to clipboard.");
    }
  };

  const handleDownload = () => {
    if (!ready) return;
    try {
      const jsonStr = JSON.stringify(payload, null, 2);
      const blob = new Blob([jsonStr], { type: "application/json" });
      const filename = `stockpy_notebookml_export_${new Date().toISOString().slice(0, 10)}.json`;

      if (typeof URL === "function" && typeof URL.createObjectURL === "function") {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      } else {
        const a = document.createElement("a");
        a.href = "data:application/json;charset=utf-8," + encodeURIComponent(jsonStr);
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      }
    } catch {
      alert("Failed to download file.");
    }
  };

  return (
    <div data-testid="notebook-export-widget" style={{ fontSize: 13 }}>
      <p style={{ color: theme.textSecondary, marginBottom: 12 }}>
        Export current portfolio positions and active strategy follows formatted for NotebookML.
      </p>

      <div>
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <button
            className="btn"
            onClick={handleCopy}
            disabled={!ready}
            data-testid="copy-export-btn"
            style={{ flex: 1, padding: "6px 12px", opacity: ready ? 1 : 0.5 }}
          >
            {copySuccess ? "Copied! ✓" : "Copy JSON"}
          </button>
          <button
            className="btn btn-neutral"
            onClick={handleDownload}
            disabled={!ready}
            data-testid="download-export-btn"
            style={{ flex: 1, padding: "6px 12px", opacity: ready ? 1 : 0.5 }}
          >
            Download JSON
          </button>
        </div>

        {!ready && (
          <div
            data-testid="export-not-ready"
            style={{ color: theme.textMuted, fontSize: 12, marginBottom: 8 }}
          >
            Waiting for portfolio data before building the export…
          </div>
        )}

        {fallbackWarning && (
          <div data-testid="clipboard-fallback-warning" style={{ color: theme.caution, fontSize: 12, marginBottom: 8 }}>
            {fallbackWarning}
          </div>
        )}

        <pre
          data-testid="export-preview"
          style={{
            background: theme.surface,
            border: `1px solid ${theme.border}`,
            padding: 8,
            borderRadius: 4,
            maxHeight: 120,
            overflowY: "auto",
            fontSize: 11,
            fontFamily: "monospace",
            color: theme.textSecondary,
            textAlign: "left",
          }}
        >
          {JSON.stringify(payload, null, 2)}
        </pre>
      </div>
    </div>
  );
}
