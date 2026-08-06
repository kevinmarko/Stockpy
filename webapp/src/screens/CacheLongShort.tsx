import { useState } from "react";
import { ConfiguratorWizard } from "../components/CacheLongShort/ConfiguratorWizard";
import { TaxDashboard } from "../components/CacheLongShort/TaxDashboard";
import { TradeApprovalCenter } from "../components/CacheLongShort/TradeApprovalCenter";
import { TabGuide } from "../components/TabGuide";
import { theme } from "../theme";

export function CacheLongShort() {
  const [activeTab, setActiveTab] = useState<"dashboard" | "config" | "approvals">("dashboard");

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", width: "100%", overflow: "hidden" }}>
      <div style={{
        padding: "16px 24px",
        borderBottom: `1px solid ${theme.border}`,
        display: "flex",
        gap: 24,
        alignItems: "center",
        flexShrink: 0
      }}>
        <h1 style={{ margin: 0, fontSize: 24, fontWeight: 600 }}>Cache Long/Short</h1>

        <div style={{ display: "flex", gap: 16 }}>
          <button 
            className={`tab ${activeTab === "dashboard" ? "active" : ""}`}
            onClick={() => setActiveTab("dashboard")}
            style={{
              background: "none",
              border: "none",
              borderBottom: activeTab === "dashboard" ? `2px solid ${theme.accent}` : "2px solid transparent",
              color: activeTab === "dashboard" ? theme.textPrimary : theme.textSecondary,
              padding: "8px 0",
              cursor: "pointer",
              fontWeight: 600
            }}
          >
            Dashboard
          </button>
          <button 
            className={`tab ${activeTab === "config" ? "active" : ""}`}
            onClick={() => setActiveTab("config")}
            style={{
              background: "none",
              border: "none",
              borderBottom: activeTab === "config" ? `2px solid ${theme.accent}` : "2px solid transparent",
              color: activeTab === "config" ? theme.textPrimary : theme.textSecondary,
              padding: "8px 0",
              cursor: "pointer",
              fontWeight: 600
            }}
          >
            Configurator
          </button>
          <button 
            className={`tab ${activeTab === "approvals" ? "active" : ""}`}
            onClick={() => setActiveTab("approvals")}
            style={{
              background: "none",
              border: "none",
              borderBottom: activeTab === "approvals" ? `2px solid ${theme.accent}` : "2px solid transparent",
              color: activeTab === "approvals" ? theme.textPrimary : theme.textSecondary,
              padding: "8px 0",
              cursor: "pointer",
              fontWeight: 600
            }}
          >
            Approvals
          </button>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: 24 }}>
        <TabGuide tabKey="cache-long-short" />
        {activeTab === "dashboard" && <TaxDashboard />}
        {activeTab === "config" && <ConfiguratorWizard />}
        {activeTab === "approvals" && <TradeApprovalCenter />}
      </div>
    </div>
  );
}
