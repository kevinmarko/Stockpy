import { useEffect, useState } from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router";
import { Dashboard } from "./screens/Dashboard";
import { Comparison } from "./screens/Comparison";
import { Marketplace } from "./screens/Marketplace";
import { PilotDetail } from "./screens/PilotDetail";
import { Portfolio } from "./screens/Portfolio";
import { SymbolDetail } from "./screens/SymbolDetail";
import { Activity } from "./screens/Activity";
import { Models } from "./screens/Models";
import { PairsRadar } from "./screens/PairsRadar";
import { OptionsMatrix } from "./screens/OptionsMatrix";
import { Attribution } from "./screens/Attribution";
import { Observability } from "./screens/Observability";
import { StrategyHealth } from "./screens/StrategyHealth";
import { Calibration } from "./screens/Calibration";
import { PipelineDashboard } from "./screens/PipelineDashboard";
import { SettingsLayout } from "./screens/SettingsLayout";
import { SettingsGeneral } from "./screens/SettingsGeneral";
import { SettingsData } from "./screens/SettingsData";
import { SettingsUniverse } from "./screens/SettingsUniverse";
import { SettingsBrokers } from "./screens/SettingsBrokers";
import { SettingsModules } from "./screens/SettingsModules";
import { StrategyMatrix } from "./screens/StrategyMatrix";
import { SettingsManager } from "./screens/SettingsManager";
import { SentimentSettings } from "./screens/SentimentSettings";
import { SectorSelectionSettings } from "./screens/SectorSelectionSettings";
import { FmpSettings } from "./screens/FmpSettings";
import { EtfTransmissionSettings } from "./screens/EtfTransmissionSettings";
import { AIControlCenter } from "./screens/AIControlCenter";
import { DataExplorer } from "./screens/DataExplorer";
import { SignalBreakdown } from "./screens/SignalBreakdown";
import { SentimentDynamics } from "./screens/SentimentDynamics";
import { ForecastViewer } from "./screens/ForecastViewer";
import { ForecastBackfillScreen } from "./screens/ForecastBackfillScreen";
import { SectorSelection } from "./screens/SectorSelection";
import { Commands } from "./screens/Commands";
import { Console } from "./screens/Console";
import { ReportLibrary } from "./screens/ReportLibrary";
import { PromptRegistry } from "./screens/PromptRegistry";
import { AgenticTrading } from "./screens/AgenticTrading";
import { ResearchHub } from "./screens/ResearchHub";
import { TradingHub } from "./screens/TradingHub";
import { OperationsHub } from "./screens/OperationsHub";
import { Help } from "./screens/Help";
import { Onboarding } from "./screens/Onboarding";
import { readOnboarding } from "./onboarding";
import { TokenGate } from "./components/TokenGate";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { CommandPaletteModal } from "./components/CommandPaletteModal";
import { ReportPreviewModal } from "./components/ReportPreviewModal";
import { TickerDrawer } from "./components/TickerDrawer";
import { TopStatusBar } from "./components/TopStatusBar";
import { ToastProvider } from "./components/ToastProvider";
import { DensityProvider } from "./components/DensityContext";
import { AutoRefreshProvider } from "./components/AutoRefreshContext";
import { needsTokenEntry } from "./auth/apiToken";
import { usePwaStatus } from "./hooks/usePwaStatus";
import { useApi } from "./hooks/useApi";
import { api } from "./api/client";
import type { CommandManifest, LlmStatus } from "./api/types";

import { theme } from "./theme";
import AIChatInterface from "./components/AIChatInterface";
import { useChat } from "./chat/ChatContext";
import { MessageSquare } from "lucide-react";
import { BottomNav, Sidebar } from "./components/BottomNavigation";

function SettingsButton() {
  const nav = useNavigate();
  const pwa = usePwaStatus();
  const { data: llm } = useApi<LlmStatus>(() => api.getLlmStatus(), []);
  const llmAttention = llm?.attention === true;
  const stateNotes = [
    pwa.needRefresh ? "update available" : null,
    llmAttention ? "AI capability needs attention" : null,
  ].filter((n): n is string => n != null);
  const settingsLabel = stateNotes.length > 0 ? `Settings (${stateNotes.join(", ")})` : "Settings";
  return (
    <button
      className="btn"
      onClick={() => nav("/settings")}
      aria-label={settingsLabel}
      data-testid="settings-button"
      style={{
        position: "fixed",
        right: 16,
        bottom: 76,
        zIndex: 40,
        width: 40,
        height: 40,
        borderRadius: "50%",
        padding: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: theme.surface2,
        border: `1px solid ${theme.borderStrong}`,
      }}
    >
      <span aria-hidden style={{ fontSize: 16 }}>
        ⚙
      </span>
      {pwa.needRefresh && (
        <span
          aria-hidden
          data-testid="pwa-update-dot"
          style={{
            position: "absolute",
            top: 2,
            right: 2,
            width: 9,
            height: 9,
            borderRadius: "50%",
            background: theme.caution,
            border: `2px solid ${theme.base}`,
          }}
        />
      )}
      {llmAttention && (
        <span
          aria-hidden
          data-testid="llm-config-dot"
          style={{
            position: "absolute",
            top: 2,
            left: 2,
            width: 9,
            height: 9,
            borderRadius: "50%",
            background: "transparent",
            border: `2px solid ${theme.caution}`,
            boxShadow: `0 0 0 1px ${theme.base}`,
          }}
        />
      )}
    </button>
  );
}

function ChatButton({ isOpen, onOpen, onClose }: { isOpen: boolean; onOpen: () => void; onClose: () => void }) {
  return (
    <button
      className="btn"
      onClick={() => (isOpen ? onClose() : onOpen())}
      aria-label="Toggle AI Chat"
      data-testid="chat-button"
      style={{
        position: "fixed",
        right: 64,
        bottom: 76,
        zIndex: 40,
        width: 40,
        height: 40,
        borderRadius: "50%",
        padding: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: theme.surface2,
        border: `1px solid ${theme.borderStrong}`,
        color: theme.textPrimary,
      }}
    >
      <MessageSquare size={18} />
    </button>
  );
}

export default function App() {
  const [done, setDone] = useState(() => readOnboarding().completed);
  const [tokenGated] = useState(() => needsTokenEntry());
  const { isOpen: isChatOpen, contextText: chatContextText, openChat, closeChat } = useChat();
  const [isPaletteOpen, setIsPaletteOpen] = useState(false);
  const [inspectedTicker, setInspectedTicker] = useState<string | null>(null);
  const [previewReportName, setPreviewReportName] = useState<string | null>(null);
  const location = useLocation();
  const navigate = useNavigate();

  const { data: commandManifest } = useApi<CommandManifest>(() => api.getCommands(), []);

  useEffect(() => {
    const handleKeyDown = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setIsPaletteOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  if (tokenGated) {
    return <TokenGate />;
  }

  if (!done) {
    return (
      <div className="app app-standalone">
        <Routes>
          <Route path="*" element={<Onboarding onDone={() => setDone(true)} />} />
        </Routes>
      </div>
    );
  }

  return (
    <ToastProvider>
      <DensityProvider>
        <AutoRefreshProvider>
          <div className="app app-shell">
            <Sidebar />
            <div className="app-main">
              <TopStatusBar />
              <ErrorBoundary key={location.pathname}>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/marketplace" element={<Marketplace />} />
                  <Route path="/compare" element={<Comparison />} />
                  <Route path="/pilots/:id" element={<PilotDetail />} />
                  <Route path="/symbol/:ticker" element={<SymbolDetail />} />
                  <Route path="/activity" element={<Activity />} />
                  <Route path="/models" element={<Models />} />
                  <Route path="/pairs" element={<PairsRadar />} />
                  <Route path="/options" element={<OptionsMatrix />} />
                  <Route path="/attribution" element={<Attribution />} />
                  <Route path="/observability" element={<Observability />} />
                  <Route path="/strategy-health" element={<StrategyHealth />} />
                  <Route path="/calibration" element={<Calibration />} />
                  <Route path="/pipeline" element={<PipelineDashboard />} />
                  <Route path="/data-explorer" element={<DataExplorer />} />
                  <Route path="/signals" element={<SignalBreakdown />} />
                  <Route path="/sentiment" element={<SentimentDynamics />} />
                  <Route path="/forecast" element={<ForecastViewer />} />
                  <Route path="/forecast/backfill" element={<ForecastBackfillScreen />} />
                  <Route path="/sector-selection" element={<SectorSelection />} />
                  <Route path="/commands" element={<Commands />} />
                  <Route path="/console" element={<Console />} />
                  <Route path="/operations/reports" element={<ReportLibrary />} />
                  <Route path="/agentic" element={<AgenticTrading />} />
                  <Route path="/research" element={<ResearchHub />} />
                  <Route path="/trading" element={<TradingHub />} />
                  <Route path="/operations" element={<OperationsHub />} />
                  <Route path="/portfolio" element={<Portfolio />} />
                  <Route path="/help" element={<Help />} />
                  <Route path="/settings" element={<SettingsLayout />}>
                    <Route index element={<SettingsGeneral />} />
                    <Route path="data" element={<SettingsData />} />
                    <Route path="universe" element={<SettingsUniverse />} />
                    <Route path="brokers" element={<SettingsBrokers />} />
                    <Route path="modules" element={<SettingsModules />} />
                    
                    <Route path="strategy" element={<StrategyMatrix />} />
                    <Route path="tunables" element={<SettingsManager />} />
                    <Route path="sentiment" element={<SentimentSettings />} />
                    <Route path="sector-selection" element={<SectorSelectionSettings />} />
                    <Route path="fmp" element={<FmpSettings />} />
                    <Route path="etf-transmission" element={<EtfTransmissionSettings />} />
                    <Route path="ai" element={<AIControlCenter />} />
                    <Route path="prompts" element={<PromptRegistry />} />
                  </Route>
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </ErrorBoundary>
            </div>
            <BottomNav />
            <ChatButton isOpen={isChatOpen} onOpen={() => openChat()} onClose={closeChat} />
            <SettingsButton />
            <AIChatInterface isOpen={isChatOpen} onClose={closeChat} contextText={chatContextText} />

            <CommandPaletteModal
              isOpen={isPaletteOpen}
              onClose={() => setIsPaletteOpen(false)}
              commands={commandManifest?.commands ?? []}
              onSelectCommandForBuilder={(spec) => {
                setIsPaletteOpen(false);
                navigate(`/commands?builder=${spec.name}`);
              }}
              onInspectTicker={(sym) => {
                setInspectedTicker(sym);
                setIsPaletteOpen(false);
              }}
              onPreviewReport={(name) => {
                setPreviewReportName(name);
                setIsPaletteOpen(false);
              }}
              onNavigate={navigate}
            />

            {inspectedTicker && (
              <TickerDrawer
                symbol={inspectedTicker}
                onClose={() => setInspectedTicker(null)}
              />
            )}

            {previewReportName && (
              <ReportPreviewModal
                name={previewReportName}
                onClose={() => setPreviewReportName(null)}
              />
            )}
          </div>
        </AutoRefreshProvider>
      </DensityProvider>
    </ToastProvider>
  );
}
