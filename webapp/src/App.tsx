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
import { Settings } from "./screens/Settings";
import { StrategyMatrix } from "./screens/StrategyMatrix";
import { SettingsManager } from "./screens/SettingsManager";
import { AIControlCenter } from "./screens/AIControlCenter";
import { DataExplorer } from "./screens/DataExplorer";
import { SignalBreakdown } from "./screens/SignalBreakdown";
import { SentimentDynamics } from "./screens/SentimentDynamics";
import { ForecastViewer } from "./screens/ForecastViewer";
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
import { needsTokenEntry } from "./auth/apiToken";
import { usePwaStatus } from "./hooks/usePwaStatus";
import { useApi } from "./hooks/useApi";
import { api } from "./api/client";
import type { CommandManifest, LlmStatus } from "./api/types";

import { theme } from "./theme";
import AIChatInterface from "./components/AIChatInterface";
import { MessageSquare } from "lucide-react";
import { BottomNav, Sidebar } from "./components/BottomNavigation";

/**
 * Fixed gear button, every screen — navigates to /settings. Formerly opened a
 * local PwaStatusDrawer bottom sheet; that content is now folded into the
 * Settings screen (a "Data & Automation" section) so the gear means one
 * thing instead of two competing "settings" affordances. Keeps the
 * needRefresh amber dot, the one thing the drawer did that a plain route
 * link can't -- surfacing "update available" from any screen without the
 * operator having to visit Settings first. Settings is ALSO listed like any
 * other screen (mobile More sheet's "Settings" group / desktop sidebar) --
 * this button is a fast-access shortcut on top of that, not the only path.
 */
function SettingsButton() {
  const nav = useNavigate();
  const pwa = usePwaStatus();
  // ONE fetch per app load -- SettingsButton lives in App's shell (outside
  // <Routes>), so it mounts once and does NOT re-mount on navigation. No
  // usePoll: LLM config changes on an operator's .env edit, not on a timer.
  // On failure `llm` stays undefined -> no dot: an absent dot is the ABSENCE
  // of a claim, never a fabricated all-clear NOR a false key alarm when the
  // real problem is the network (the Settings screen shows the honest error).
  const { data: llm } = useApi<LlmStatus>(() => api.getLlmStatus(), []);
  const llmAttention = llm?.attention === true;
  // Both status dots below are aria-hidden (decorative — small color-only
  // marks), so the condition they signal is named HERE instead, on the
  // interactive element itself. Previously the llm dot's `title` carried the
  // only text description of that state, but a `title` on an aria-hidden
  // element is never exposed to assistive tech — announced to nobody, and
  // never available on touch either. This label is the fix, not the dot.
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
        bottom: 76, // clears the mobile bottom-nav; desktop has no bottom-nav so this just floats
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
        // Deliberately a RING, not a filled dot like pwa-update-dot above --
        // same color/size/corner-adjacent position previously made these two
        // marks indistinguishable at a glance even for a sighted user; shape
        // is now a second, position-independent cue. The condition itself is
        // named on the button's aria-label above, not here (aria-hidden).
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

/** Fixed chat button to toggle the global AI Chat Interface. */
function ChatButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      className="btn"
      onClick={onClick}
      aria-label="Toggle AI Chat"
      data-testid="chat-button"
      style={{
        position: "fixed",
        right: 64, // Positioned to the left of SettingsButton
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
  // TokenGate reloads the page after storing a token (see its own comment),
  // so this only ever needs to be read once per mount -- no setter to wire.
  const [tokenGated] = useState(() => needsTokenEntry());
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [isPaletteOpen, setIsPaletteOpen] = useState(false);
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
    <div className="app app-shell">
      <Sidebar />
      <div className="app-main">
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
            <Route path="/sector-selection" element={<SectorSelection />} />
            <Route path="/commands" element={<Commands />} />
            <Route path="/console" element={<Console />} />
            <Route path="/operations/reports" element={<ReportLibrary />} />
            <Route path="/help" element={<Help />} />
            <Route path="/agentic" element={<AgenticTrading />} />
            <Route path="/research" element={<ResearchHub />} />
            <Route path="/trading" element={<TradingHub />} />
            <Route path="/operations" element={<OperationsHub />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/settings/strategy" element={<StrategyMatrix />} />
            <Route path="/settings/tunables" element={<SettingsManager />} />
            <Route path="/settings/ai" element={<AIControlCenter />} />
            <Route path="/settings/prompts" element={<PromptRegistry />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </ErrorBoundary>
      </div>
      <BottomNav />
      <ChatButton onClick={() => setIsChatOpen(!isChatOpen)} />
      <SettingsButton />
      <AIChatInterface isOpen={isChatOpen} onClose={() => setIsChatOpen(false)} />
      <CommandPaletteModal
        isOpen={isPaletteOpen}
        onClose={() => setIsPaletteOpen(false)}
        commands={commandManifest?.commands ?? []}
        onSelectCommandForBuilder={(spec) => {
          setIsPaletteOpen(false);
          navigate(`/commands?builder=${spec.name}`);
        }}
      />
    </div>
  );
}
