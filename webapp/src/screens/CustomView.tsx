import { Link, useNavigate, useParams } from "react-router";
import { MessageCircle, Trash2 } from "lucide-react";
import { Button, EmptyState } from "../components/ui";
import { EdgeByStrategyChart } from "../components/EdgeByStrategyChart";
import { SymbolSignalOverlayChart } from "../components/SymbolSignalOverlayChart";
import { PilotsTableWidget } from "../components/PilotsTableWidget";
import { SentimentMiniChart } from "../components/SentimentMiniChart";
import { PortfolioHeatWidget } from "../components/PortfolioHeatWidget";
import { OptionsDirectiveSummary } from "../components/OptionsDirectiveSummary";
import { SignalBreakdownMiniWidget } from "../components/SignalBreakdownMiniWidget";
import { MacroRegimeBanner } from "../components/MacroRegimeBanner";
import { TabGuide } from "../components/TabGuide";
import { useChat } from "../chat/ChatContext";
import { useCustomViews } from "../customViews";
import { theme } from "../theme";
import toast from "react-hot-toast";

/**
 * /app/:slug -- renders one operator-saved Data App (see
 * screens/CreateDataApp.tsx). Every widget here is the SAME component
 * StrategyInsights.tsx renders -- no bespoke chart/chat implementation of
 * its own, so none of PR #670's original bugs (fabricated simulation,
 * unauthenticatable bespoke chat endpoint) have anywhere to reappear.
 */
export function CustomView() {
  const { slug = "" } = useParams();
  const navigate = useNavigate();
  const { views, removeView } = useCustomViews();
  const { openChat } = useChat();

  const view = views.find((v) => v.slug === slug);

  if (!view) {
    return (
      <div className="screen">
        <h1 className="screen-title">Data App not found</h1>
        <div style={{ marginTop: "var(--s-4)" }}>
          <EmptyState
            title="No Data App saved at this address"
            hint="It may have been deleted, or the link is stale."
          />
          <div style={{ marginTop: "var(--s-3)" }}>
            <Link to="/create-data-app" className="btn btn-primary">
              Go to Create Data App
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const handleDelete = () => {
    const { persisted } = removeView(view.id);
    if (persisted) {
      toast.success(`Removed "${view.name}" from the sidebar.`);
    } else {
      toast.error(`Removed "${view.name}" for this session, but your browser didn't allow the removal to be saved permanently.`);
    }
    navigate("/create-data-app");
  };

  return (
    <div className="screen">
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "var(--s-3)" }}>
        <h1 className="screen-title">{view.name}</h1>
        <Button variant="neutral" onClick={handleDelete} data-testid="custom-view-delete">
          <span aria-hidden style={{ display: "inline-flex", alignItems: "center", gap: "var(--s-1-5)" }}>
            <Trash2 size={16} strokeWidth={2.5} /> Delete
          </span>
        </Button>
      </div>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-body)", marginTop: "var(--s-1)" }}>
        A custom Data App you saved. Manage all of your Data Apps from{" "}
        <Link to="/create-data-app">Create Data App</Link>.
      </p>

      <TabGuide tabKey="custom-view" />

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)", marginTop: "var(--s-4)" }}>
        {view.widgets.edgeByStrategy && (
          <section className="card card-pad">
            <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Edge per strategy</h2>
            <EdgeByStrategyChart />
          </section>
        )}

        {view.widgets.symbolOverlay && (
          <section className="card card-pad">
            <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>
              Price history &amp; signal overlay
            </h2>
            <SymbolSignalOverlayChart />
          </section>
        )}

        {view.widgets.pilotsTable && (
          <section className="card card-pad">
            <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Pilots</h2>
            <PilotsTableWidget />
          </section>
        )}

        {view.widgets.sentimentMini && (
          <section className="card card-pad">
            <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Sentiment history</h2>
            <SentimentMiniChart />
          </section>
        )}

        {view.widgets.portfolioHeat && (
          <section className="card card-pad">
            <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Portfolio heat</h2>
            <PortfolioHeatWidget />
          </section>
        )}

        {view.widgets.optionsDirective && (
          <section className="card card-pad">
            <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Options directives</h2>
            <OptionsDirectiveSummary />
          </section>
        )}

        {view.widgets.signalBreakdown && (
          <section className="card card-pad">
            <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Signal breakdown</h2>
            <SignalBreakdownMiniWidget />
          </section>
        )}

        {view.widgets.macroRegime && (
          <section className="card card-pad">
            <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Macro regime</h2>
            <MacroRegimeBanner />
          </section>
        )}

        {view.widgets.aiChat && (
          <section className="card card-pad">
            <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-2)" }}>Ask AI</h2>
            <p style={{ color: theme.textMuted, fontSize: "var(--t-body)", marginBottom: "var(--s-3)" }}>
              Opens the platform's grounded chat assistant with this view's context.
            </p>
            <Button
              variant="primary"
              onClick={() => openChat(`Operator is viewing their custom Data App "${view.name}".`)}
              data-testid="custom-view-open-chat"
            >
              <span aria-hidden style={{ display: "inline-flex", alignItems: "center", gap: "var(--s-1-5)" }}>
                <MessageCircle size={16} strokeWidth={2.5} /> Ask AI about this view
              </span>
            </Button>
          </section>
        )}
      </div>
    </div>
  );
}
