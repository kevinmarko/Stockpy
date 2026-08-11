import { useState } from "react";
import { useNavigate } from "react-router";
import toast from "react-hot-toast";
import { LayoutTemplate, Trash2 } from "lucide-react";
import { Button, EmptyState, Input } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { useCustomViews, type CustomViewWidgets } from "../customViews";
import { theme } from "../theme";

const WIDGET_LABELS: Record<keyof CustomViewWidgets, string> = {
  edgeByStrategy: "Edge-by-strategy chart",
  symbolOverlay: "Symbol price + signal overlay chart",
  aiChat: "“Ask AI about this view” chat shortcut",
  pilotsTable: "Pilots holdings table",
  sentimentMini: "Sentiment history mini-chart",
  portfolioHeat: "Portfolio heat gauge",
  optionsDirective: "Options directive summary",
  signalBreakdown: "Signal breakdown mini-chart",
  macroRegime: "Macro regime banner",
};

// The original 3 widgets default ON (a useful view with zero configuration);
// the 6 added later default OFF so a first-time "Create" isn't an
// unexpectedly heavy 9-widget page -- an operator opts into the rest.
const DEFAULT_WIDGETS: CustomViewWidgets = {
  edgeByStrategy: true,
  symbolOverlay: true,
  aiChat: true,
  pilotsTable: false,
  sentimentMini: false,
  portfolioHeat: false,
  optionsDirective: false,
  signalBreakdown: false,
  macroRegime: false,
};

/**
 * Create Data App -- names and saves a custom view (a real, persisted
 * sidebar shortcut to a page built from real, reused widgets), and manages
 * the operator's existing saved views.
 *
 * This is the one capability PR #670 attempted that doesn't already exist
 * elsewhere on main (see StrategyInsights.tsx's header comment for why that
 * screen deliberately does NOT do this). Built honestly this time: creating
 * a view here actually persists it (customViews.ts, backed by
 * localStorage + useSyncExternalStore) and actually adds a working nav
 * entry (navigation.tsx's useNavItems()) -- no backend stub, no fabricated
 * "success" response.
 */
export function CreateDataApp() {
  const navigate = useNavigate();
  const { views, addOrUpdateView, removeView } = useCustomViews();

  const [name, setName] = useState("");
  const [widgets, setWidgets] = useState<CustomViewWidgets>(DEFAULT_WIDGETS);

  const trimmedName = name.trim();
  const anyWidgetSelected = Object.values(widgets).some(Boolean);
  const canCreate = trimmedName.length > 0 && anyWidgetSelected;

  const toggleWidget = (key: keyof CustomViewWidgets) => {
    setWidgets((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const handleCreate = () => {
    if (!canCreate) return;
    const { view, persisted } = addOrUpdateView({ name: trimmedName, widgets });
    if (persisted) {
      toast.success(`Saved "${view.name}" to the sidebar.`);
    } else {
      // Real localStorage write failed (quota exceeded, private-mode storage
      // block, etc.) -- the view still works for THIS tab's current session,
      // but will not survive a reload. Never claim "Saved" when it wasn't
      // (CONSTRAINT #4) -- see customViews.ts's persist() doc.
      toast.error(`"${view.name}" is only available for this session -- your browser didn't allow it to be saved permanently.`);
    }
    setName("");
    setWidgets(DEFAULT_WIDGETS);
    navigate(`/app/${view.slug}`);
  };

  const handleDelete = (id: string, viewName: string) => {
    const { persisted } = removeView(id);
    if (persisted) {
      toast.success(`Removed "${viewName}" from the sidebar.`);
    } else {
      toast.error(`Removed "${viewName}" for this session, but your browser didn't allow the removal to be saved permanently.`);
    }
  };

  return (
    <div className="screen">
      <h1 className="screen-title">Create Data App</h1>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-body)", marginTop: "var(--s-1)" }}>
        Name a custom view built from real, live widgets and save it as a working
        sidebar shortcut.
      </p>

      <TabGuide tabKey="create-data-app" />

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)", marginTop: "var(--s-4)" }}>
        <section className="card card-pad">
          <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>New Data App</h2>

          <div style={{ maxWidth: 360, marginBottom: "var(--s-3)" }}>
            <Input
              label="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Momentum Desk"
            />
          </div>

          <div style={{ marginBottom: "var(--s-3)" }}>
            <div className="tile-label" style={{ marginBottom: "var(--s-1-5)" }}>
              Widgets
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
              {(Object.keys(WIDGET_LABELS) as (keyof CustomViewWidgets)[]).map((key) => (
                <label
                  key={key}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--s-2)",
                    fontSize: "var(--t-caption)",
                    cursor: "pointer",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={widgets[key]}
                    onChange={() => toggleWidget(key)}
                    data-testid={`widget-toggle-${key}`}
                  />
                  {WIDGET_LABELS[key]}
                </label>
              ))}
            </div>
            {!anyWidgetSelected && (
              <div style={{ marginTop: "var(--s-1-5)", fontSize: "var(--t-caption)", color: "var(--decline)" }}>
                Pick at least one widget.
              </div>
            )}
          </div>

          <Button variant="primary" disabled={!canCreate} onClick={handleCreate} data-testid="create-data-app-submit">
            Create &amp; save to sidebar
          </Button>
        </section>

        <section className="card card-pad">
          <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Your Data Apps</h2>
          {views.length === 0 ? (
            <EmptyState title="No Data Apps yet" hint="Create one above -- it'll show up here and in the sidebar." />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }} data-testid="data-app-list">
              {views.map((v) => (
                <div
                  key={v.id}
                  data-testid={`data-app-row-${v.slug}`}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: "var(--s-2)",
                    padding: "var(--s-2-5) var(--s-3)",
                    border: `1px solid ${theme.border}`,
                    borderRadius: 8,
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", minWidth: 0 }}>
                    <span aria-hidden style={{ display: "inline-flex", color: theme.textMuted }}>
                      <LayoutTemplate size={16} strokeWidth={2.5} />
                    </span>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 600 }}>{v.name}</div>
                      <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted }}>
                        {(Object.keys(WIDGET_LABELS) as (keyof CustomViewWidgets)[])
                          .filter((k) => v.widgets[k])
                          .map((k) => WIDGET_LABELS[k])
                          .join(" · ") || "No widgets"}
                      </div>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "var(--s-2)", flexShrink: 0 }}>
                    <Button variant="neutral" onClick={() => navigate(`/app/${v.slug}`)} data-testid={`data-app-open-${v.slug}`}>
                      Open
                    </Button>
                    <Button
                      variant="neutral"
                      onClick={() => handleDelete(v.id, v.name)}
                      data-testid={`data-app-delete-${v.slug}`}
                      aria-label={`Delete ${v.name}`}
                    >
                      <span aria-hidden style={{ display: "inline-flex", alignItems: "center" }}>
                        <Trash2 size={16} strokeWidth={2.5} />
                      </span>
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
