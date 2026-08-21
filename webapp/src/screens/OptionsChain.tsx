import React, { useState } from "react";
import { useParams, useNavigate } from "react-router";
import { ArrowLeft, Settings } from "lucide-react";
import { useApi } from "../hooks/useApi";
import { api } from "../api/client";
import { OptionChainResponse } from "../api/types";
import { theme, alpha } from "../theme";
import { fmtUsd } from "../format";
import { OptionsChain as OptionsChainGrid } from "../components/options/OptionsChain";
import { OptionsOrderTicket } from "../components/options/OptionsOrderTicket";
import { OptionsMetricSelector, MetricColumn } from "../components/options/OptionsMetricSelector";
import { OptionsStrategyBuilder } from "../components/options/OptionsStrategyBuilder";
import { EarningsCrushScanner } from "../components/options/EarningsCrushScanner";
import { UnusualFlowFeed } from "../components/options/UnusualFlowFeed";
import { VolForecastScanner } from "../components/options/VolForecastScanner";
import { GammaScalperView } from "../components/options/GammaScalperView";
import { DispersionScanner } from "../components/options/DispersionScanner";
import { ZeroDteDesk } from "../components/options/ZeroDteDesk";
import { VpinGauge } from "../components/options/VpinGauge";
import { SmartOrderRouterView } from "../components/options/SmartOrderRouterView";
import { GexProfileView } from "../components/options/GexProfileView";
import { LobDepthView } from "../components/options/LobDepthView";
import { CopulaSpreadView } from "../components/options/CopulaSpreadView";
import { MarketMakerAgentView } from "../components/options/MarketMakerAgentView";
import { TransformerVolForecastView } from "../components/charts/TransformerVolForecastView";
import { GenerativeDiffusionStressView } from "../components/charts/GenerativeDiffusionStressView";
import { VolSurface3D } from "../components/charts/VolSurface3D";
import { LobDepth3D } from "../components/charts/LobDepth3D";
import { TabGuide } from "../components/TabGuide";

type ChainTab = "calls" | "puts" | "volsurf3d" | "lob3d" | "flow" | "crush" | "forecast" | "gamma" | "dispersion" | "zerodte" | "vpin" | "sor" | "gex" | "lob" | "copula" | "mm" | "aivol" | "stress";

export function OptionsChain() {
  const { ticker } = useParams<{ ticker: string }>();
  const navigate = useNavigate();
  const [selectedExp, setSelectedExp] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<ChainTab>("calls");

  const [selectedMetrics, setSelectedMetrics] = useState<MetricColumn[]>(['volume', 'openInterest', 'delta', 'chanceOfProfit']);
  const [isMetricSelectorOpen, setIsMetricSelectorOpen] = useState(false);
  const [selectedLegs, setSelectedLegs] = useState<{ contract: any, type: 'call' | 'put', action: 'Buy' | 'Sell' }[]>([]);
  const [isBuilderMode, setIsBuilderMode] = useState(false);
  const [isStockTradeOpen, setIsStockTradeOpen] = useState(false);

  // Fetch the full expirations list once per symbol, independent of whichever
  // expiration is currently selected. The per-expiration chain fetch below
  // never carries its own `expirations` array (both the mock and live backend
  // omit it once `expiration` is passed) -- deriving the scroller/list from
  // `chainData` directly collapsed to a single stale entry the instant an
  // expiration was selected.
  const { data: expirationsData } = useApi<OptionChainResponse>(
    () => api.getOptionsChain(ticker!),
    [ticker]
  );
  const expirations = expirationsData?.expirations || [];

  // Fetch chain for the selected expiration
  const { data: chainData, loading, error } = useApi<OptionChainResponse>(
    () => api.getOptionsChain(ticker!, selectedExp || undefined),
    [ticker, selectedExp]
  );

  // Auto-select the first expiration when they load
  React.useEffect(() => {
    if (!selectedExp && expirations.length > 0) {
      setSelectedExp(expirations[0]);
    }
  }, [expirations, selectedExp]);

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
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: "0.85rem", color: theme.textSecondary }}>Share price</span>
          <span style={{ fontSize: "0.95rem", fontWeight: 600 }}>
            {chainData?.spot_price != null ? fmtUsd(chainData.spot_price) : "—"}
          </span>
        </div>
        <button
          onClick={() => {
            setIsStockTradeOpen(true);
            setSelectedLegs([]);
          }}
          style={{
            background: alpha(theme.accent, "20"),
            border: `1px solid ${theme.accent}`,
            color: theme.accent,
            borderRadius: 14,
            padding: "4px 12px",
            fontSize: "0.8rem",
            fontWeight: 600,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 6,
            transition: "all 0.15s ease"
          }}
        >
          📈 Trade {ticker} Stock
        </button>
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

      {/* Calls / Puts / Unusual Flow / Earnings Crush Tabs */}
      <div style={{
        display: "flex",
        padding: "8px 16px",
        gap: 4,
        borderBottom: `1px solid ${theme.border}`,
        flexShrink: 0
      }}>
        {[
          { key: "calls", label: "Calls" },
          { key: "puts", label: "Puts" },
          { key: "volsurf3d", label: "🌐 3D Surface" },
          { key: "lob3d", label: "📊 3D LOB" },
          { key: "gex", label: "📊 GEX Profile" },
          { key: "lob", label: "🪜 LOB Depth" },
          { key: "copula", label: "🔗 Copula" },
          { key: "mm", label: "🤖 MM Agent" },
          { key: "forecast", label: "🎯 Vol Scanner" },
          { key: "aivol", label: "🤖 AI Vol" },
          { key: "stress", label: "🌪️ Stress" },
          { key: "gamma", label: "⚡ Gamma Scalp" },
          { key: "dispersion", label: "🌐 Dispersion" },
          { key: "zerodte", label: "⚡ 0DTE" },
          { key: "vpin", label: "⏱ VPIN" },
          { key: "sor", label: "🔀 Smart Router" },
          { key: "flow", label: "🌊 Flow" },
          { key: "crush", label: "⚡ Crush" },
        ].map(tab => {
          const isActive = tab.key === activeTab;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key as ChainTab)}
              style={{
                flex: 1,
                padding: "8px 0",
                background: isActive ? theme.surface3 : "transparent",
                color: isActive ? theme.textPrimary : theme.textSecondary,
                border: "none",
                borderRadius: 8,
                fontWeight: isActive ? 600 : 400,
                fontSize: "0.85rem",
                cursor: "pointer",
                transition: "all 0.15s ease",
                whiteSpace: "nowrap",
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Main Content Area */}
      <div style={{ flex: 1, overflowY: "auto", padding: 16, paddingBottom: (selectedLegs.length > 0 || isStockTradeOpen) ? 350 : 16 }}>
        <TabGuide tabKey="options-chain" />
        
        {activeTab === "volsurf3d" ? (
          <VolSurface3D symbol={ticker || "SPY"} spotPrice={spotPrice || 505.20} />
        ) : activeTab === "lob3d" ? (
          <LobDepth3D symbol={ticker || "SPY"} currentPrice={spotPrice || 505.20} />
        ) : activeTab === "copula" ? (
          <CopulaSpreadView initialPair={`${ticker || "SPY"}/QQQ`} />
        ) : activeTab === "mm" ? (
          <MarketMakerAgentView initialSymbol={ticker || "SPY"} spotPrice={spotPrice || 546.50} />
        ) : activeTab === "gex" ? (
          <GexProfileView initialSymbol={ticker || "SPY"} spotPrice={spotPrice || 546.50} />
        ) : activeTab === "lob" ? (
          <LobDepthView initialSymbol={ticker || "SPY"} spotPrice={spotPrice || 546.50} />
        ) : activeTab === "forecast" ? (
          <VolForecastScanner initialSymbol={ticker} />
        ) : activeTab === "aivol" ? (
          <TransformerVolForecastView symbol={ticker || "SPY"} />
        ) : activeTab === "stress" ? (
          <GenerativeDiffusionStressView symbol={ticker || "SPY"} spotPrice={spotPrice || 505.20} />
        ) : activeTab === "gamma" ? (
          <GammaScalperView initialSymbol={ticker} spotPrice={spotPrice} />
        ) : activeTab === "dispersion" ? (
          <DispersionScanner initialIndex={ticker} />
        ) : activeTab === "zerodte" ? (
          <ZeroDteDesk initialSymbol={ticker} />
        ) : activeTab === "vpin" ? (
          <VpinGauge initialSymbol={ticker || "SPY"} />
        ) : activeTab === "sor" ? (
          <SmartOrderRouterView initialSymbol={ticker || "SPY"} spotPrice={spotPrice || 546.50} />
        ) : activeTab === "flow" ? (
          <UnusualFlowFeed initialSymbol={ticker} />
        ) : activeTab === "crush" ? (
          <EarningsCrushScanner initialSymbols={ticker ? [ticker] : undefined} />
        ) : isBuilderMode ? (
          <OptionsStrategyBuilder
            symbol={ticker!}
            chain={chainData || null}
            expirations={expirations}
            selectedLegs={selectedLegs}
            onUpdateLegs={(legs) => {
              setSelectedLegs(legs);
              setIsStockTradeOpen(false);
            }}
          />
        ) : (
          chainData && (
            <OptionsChainGrid
              data={chainData}
              activeTab={activeTab as "calls" | "puts"}
              selectedMetrics={selectedMetrics}
              onSelectContract={(contract, type) => {
                setSelectedLegs([{ contract, type, action: 'Buy' }]);
                setIsStockTradeOpen(false);
              }}
            />
          )
        )}
      </div>

      {/* Floating Order Ticket */}
      {(selectedLegs.length > 0 || isStockTradeOpen) && (
        <div style={{
          position: 'absolute',
          bottom: 16,
          left: 16,
          right: 16,
          zIndex: 100,
          display: 'flex',
          justifyContent: 'center'
        }}>
          <div style={{ width: '100%', maxWidth: 520, boxShadow: '0 8px 32px rgba(0,0,0,0.5)', borderRadius: 16, overflow: 'hidden' }}>
            <OptionsOrderTicket
              // Force a fresh instance (and thus a full internal-state reset --
              // limit price, order type, sizing, live toggle) whenever the
              // trade context switches between stock mode and a specific
              // option leg, instead of silently reusing stale state from a
              // previous selection.
              key={isStockTradeOpen ? `stock-${ticker}` : `option-${selectedLegs.map(l => `${l.type}-${l.contract.strike}-${l.action}`).join('|')}-${selectedExp || ''}`}
              symbol={ticker!}
              expiration={selectedExp || undefined}
              legs={selectedLegs}
              assetType={isStockTradeOpen ? "stock" : "option"}
              spotPrice={chainData?.spot_price ?? null}
              onClear={() => {
                setSelectedLegs([]);
                setIsStockTradeOpen(false);
              }}
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
