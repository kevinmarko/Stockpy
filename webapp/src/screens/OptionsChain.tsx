import React, { useState } from "react";
import { useParams, useNavigate } from "react-router";
import { ArrowLeft, Settings } from "lucide-react";
import { useApi } from "../hooks/useApi";
import { api } from "../api/client";
import { OptionChainResponse } from "../api/types";
import { theme } from "../theme";
import { OptionsChain as OptionsChainGrid } from "../components/options/OptionsChain";
import { OptionsOrderTicket } from "../components/options/OptionsOrderTicket";
import { OptionsMetricSelector, MetricColumn } from "../components/options/OptionsMetricSelector";
import { OptionsStrategyBuilder } from "../components/options/OptionsStrategyBuilder";
import { TabGuide } from "../components/TabGuide";

type ChainTab = "calls" | "puts";

export function OptionsChain() {
  const { ticker } = useParams<{ ticker: string }>();
  const navigate = useNavigate();
  const [selectedExp, setSelectedExp] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<ChainTab>("calls");

  const [selectedMetrics, setSelectedMetrics] = useState<MetricColumn[]>(['volume', 'openInterest', 'delta', 'chanceOfProfit']);
  const [isMetricSelectorOpen, setIsMetricSelectorOpen] = useState(false);
  const [selectedLegs, setSelectedLegs] = useState<{ contract: any, type: 'call' | 'put', action: 'Buy' | 'Sell' }[]>([]);
  const [isBuilderMode, setIsBuilderMode] = useState(false);

  // Fetch chain for selected expiration (or expirations list when none selected)
  const { data: chainData, loading, error } = useApi<OptionChainResponse>(
    () => api.getOptionsChain(ticker!, selectedExp || undefined),
    [ticker, selectedExp]
  );

  const expirations = chainData?.expirations || (chainData?.expiration ? [chainData.expiration] : []);

  // Auto-select the first expiration when they load
  React.useEffect(() => {
    if (!selectedExp && expirations.length > 0 && !chainData?.expiration) {
      setSelectedExp(expirations[0]);
    }
  }, [expirations, selectedExp, chainData]);

  if (loading && !chainData) {
    return <div style={{ padding: 16 }}>Loading options chain...</div>;
  }

  if (error) {
    return <div style={{ padding: 16, color: theme.decline }}>Error loading options: {String(error)}</div>;
  }

  const spotPrice = chainData?.spot_price || 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: theme.base, color: theme.textPrimary, overflow: "hidden" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: `1px solid ${theme.border}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button className="btn btn-ghost" onClick={() => navigate(-1)} style={{ padding: 4 }}>
            <ArrowLeft size={20} />
          </button>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span style={{ fontWeight: 600, fontSize: "1.1rem" }}>{ticker}</span>
            <span style={{ fontSize: "0.75rem", color: theme.textSecondary }}>Options Chain</span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button 
            className="btn btn-ghost" 
            style={{ padding: 4 }}
            onClick={() => setIsMetricSelectorOpen(true)}
          >
            <Settings size={20} />
          </button>
        </div>
      </div>

      {/* Share Price Banner */}
      <div style={{
        padding: "8px 16px",
        background: theme.surface,
        borderBottom: `1px solid ${theme.border}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between"
      }}>
        <span style={{ fontSize: "0.85rem", color: theme.textSecondary }}>Share price</span>
        <span style={{ fontSize: "0.95rem", fontWeight: 600 }}>${spotPrice.toFixed(2)}</span>
      </div>

      {/* Expirations Scroller */}
      <div style={{
        display: "flex",
        overflowX: "auto",
        padding: "8px 16px",
        gap: 8,
        borderBottom: `1px solid ${theme.border}`,
        scrollbarWidth: "none",
        flexShrink: 0
      }}>
        <button
          onClick={() => setIsBuilderMode(!isBuilderMode)}
          style={{
            background: isBuilderMode ? theme.accent : "transparent",
            color: isBuilderMode ? "#000" : theme.accent,
            border: `1px solid ${theme.accent}`,
            borderRadius: 16,
            padding: "6px 14px",
            fontWeight: 600,
            fontSize: "0.85rem",
            cursor: "pointer",
            whiteSpace: "nowrap",
            transition: "all 0.15s ease",
            display: 'flex',
            alignItems: 'center',
            gap: 4
          }}
        >
          ⊞ Builder
        </button>

        <div style={{ width: 1, background: theme.borderStrong, margin: '4px 4px' }} />

        {expirations.map(exp => {
          const isSelected = exp === selectedExp && !isBuilderMode;
          return (
            <button
              key={exp}
              onClick={() => {
                setSelectedExp(exp);
                setIsBuilderMode(false);
              }}
              style={{
                background: isSelected ? theme.growth : "transparent",
                color: isSelected ? "#000" : theme.textSecondary,
                border: "none",
                borderRadius: 16,
                padding: "6px 14px",
                fontWeight: isSelected ? 600 : 400,
                fontSize: "0.85rem",
                cursor: "pointer",
                whiteSpace: "nowrap",
                transition: "all 0.15s ease"
              }}
            >
              {exp}
            </button>
          );
        })}
      </div>

      {/* Calls / Puts Toggle */}
      <div style={{
        display: "flex",
        padding: "8px 16px",
        gap: 4,
        borderBottom: `1px solid ${theme.border}`,
        flexShrink: 0
      }}>
        {(["calls", "puts"] as ChainTab[]).map(tab => {
          const isActive = tab === activeTab;
          return (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                flex: 1,
                padding: "8px 0",
                background: isActive ? theme.surface3 : "transparent",
                color: isActive ? theme.textPrimary : theme.textSecondary,
                border: "none",
                borderRadius: 8,
                fontWeight: isActive ? 600 : 400,
                fontSize: "0.9rem",
                cursor: "pointer",
                textTransform: "capitalize",
                transition: "all 0.15s ease"
              }}
            >
              {tab}
            </button>
          );
        })}
      </div>

      {/* Chain Grid or Builder */}
      <div style={{ flex: 1, overflowY: "auto", padding: 16, paddingBottom: selectedLegs.length > 0 ? 300 : 16 }}>
        <TabGuide tabKey="options-chain" />
        
        {isBuilderMode ? (
          <OptionsStrategyBuilder
            chain={chainData || null}
            selectedLegs={selectedLegs}
            onUpdateLegs={setSelectedLegs}
          />
        ) : (
          chainData && (
            <OptionsChainGrid
              data={chainData}
              activeTab={activeTab}
              selectedMetrics={selectedMetrics}
              onSelectContract={(contract, type) => {
                setSelectedLegs([{ contract, type, action: 'Buy' }]);
              }}
            />
          )
        )}
      </div>

      {/* Floating Order Ticket */}
      {selectedLegs.length > 0 && selectedExp && (
        <div style={{
          position: 'absolute',
          bottom: 16,
          left: 16,
          right: 16,
          zIndex: 100,
          display: 'flex',
          justifyContent: 'center'
        }}>
          <div style={{ width: '100%', maxWidth: 500, boxShadow: '0 8px 32px rgba(0,0,0,0.4)', borderRadius: 16, overflow: 'hidden' }}>
            <OptionsOrderTicket
              symbol={ticker!}
              expiration={selectedExp}
              legs={selectedLegs}
              onClear={() => setSelectedLegs([])}
            />
          </div>
        </div>
      )}

      {/* Metric Selector Modal */}
      {isMetricSelectorOpen && (
        <OptionsMetricSelector
          selectedMetrics={selectedMetrics}
          onChange={setSelectedMetrics}
          onClose={() => setIsMetricSelectorOpen(false)}
        />
      )}
    </div>
  );
}
