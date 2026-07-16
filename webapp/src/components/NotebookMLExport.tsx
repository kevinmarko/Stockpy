import { useState, useMemo } from "react";
import { api } from "../api/client";
import type { Portfolio, Follow } from "../api/types";
import { useApi } from "../hooks/useApi";
import { theme } from "../theme";

interface NotebookMLExportProps {
  portfolio?: Portfolio | null;
}

export function NotebookMLExport({ portfolio }: NotebookMLExportProps = {}) {
  const [copySuccess, setCopySuccess] = useState(false);
  const [fallbackWarning, setFallbackWarning] = useState("");
  
  const portApi = useApi<Portfolio>(() => portfolio ? Promise.resolve(portfolio) : api.getPortfolio(), [portfolio]);
  const portData = portfolio !== undefined ? portfolio : portApi.data;
  const follows = useApi<Follow[]>(() => api.getFollows(), []);

  const payload = useMemo(() => {
    // We handle the loading state by returning an empty shell or keeping arrays empty
    const positions = portData?.positions || [];
    const total_equity = portData?.total_equity ?? 0;
    const buying_power = portData?.buying_power ?? 0;
    
    const followed_pilots = (follows.data ?? []).map(f => ({
      pilot_id: f.pilot_id,
      amount: f.amount,
      status: f.status,
    }));

    let layoutMetadata: any = null;
    try {
      const savedLayout = localStorage.getItem("dashboard_layout");
      if (savedLayout) {
        layoutMetadata = JSON.parse(savedLayout);
      }
    } catch (e) {
      // ignore
    }

    return {
      timestamp: new Date().toISOString(),
      portfolio: {
        total_equity,
        buying_power,
        positions: positions.map(p => ({
          symbol: p.symbol || "",
          qty: p.qty ?? 0,
          avg_cost: p.avg_cost ?? 0,
          market_value: p.market_value ?? 0,
          description: (p as any).description ? String((p as any).description) : "",
        })),
      },
      followed_pilots,
      dashboard_layout: layoutMetadata,
    };
  }, [portData, follows.data]);

  const handleCopy = async () => {
    if (!payload) return;
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
    if (!payload) return;
    try {
      const jsonStr = JSON.stringify(payload, null, 2);
      const blob = new Blob([jsonStr], { type: "application/json" });

      if (typeof URL === "function" && typeof URL.createObjectURL === "function") {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `stockpy_notebookml_export_${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      } else {
        const a = document.createElement("a");
        a.href = "data:application/json;charset=utf-8," + encodeURIComponent(jsonStr);
        a.download = `stockpy_notebookml_export_${new Date().toISOString().slice(0, 10)}.json`;
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
            data-testid="copy-export-btn"
            style={{ flex: 1, padding: "6px 12px" }}
          >
            {copySuccess ? "Copied! ✓" : "Copy JSON"}
          </button>
          <button
            className="btn btn-neutral"
            onClick={handleDownload}
            data-testid="download-export-btn"
            style={{ flex: 1, padding: "6px 12px" }}
          >
            Download JSON
          </button>
        </div>

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
