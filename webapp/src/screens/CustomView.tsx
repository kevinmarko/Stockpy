import { Link, useNavigate, useParams } from "react-router";
import { MessageCircle, Trash2 } from "lucide-react";
import { Button, EmptyState } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { useChat } from "../chat/ChatContext";
import { useCustomViews } from "../customViews";
import { WIDGET_COMPONENTS, type NonChatWidgetKey } from "../widgetRegistry";
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
        {view.widgetOrder.map((key) => {
          const config = view.widgetConfigs?.[key] || {};

          // `aiChat` is intentionally not in WIDGET_COMPONENTS -- it needs
          // the real `useChat()` context, unlike every other widget here.
          // See widgetRegistry.tsx's module doc for the full reasoning.
          if (key === "aiChat") {
            return (
              <section key={key} className="card card-pad">
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
            );
          }

          const entry = WIDGET_COMPONENTS[key as NonChatWidgetKey];
          if (!entry) return null; // unknown/stale key -- degrade silently, same as the old switch's `default`.
          const { Component, heading } = entry;
          return (
            <section key={key} className="card card-pad">
              <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>{heading}</h2>
              <Component {...config} />
            </section>
          );
        })}
      </div>
    </div>
  );
}
