import { useState, useRef } from "react";
import { useNavigate } from "react-router";
import toast from "react-hot-toast";
import { LayoutTemplate, Trash2, Edit2, Copy, Download, Upload, GripVertical, Settings } from "lucide-react";
import { Reorder } from "framer-motion";
import { Button, EmptyState, Input } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { useCustomViews, type CustomViewWidgets, type CustomView } from "../customViews";
import { theme } from "../theme";

import { EdgeByStrategyChart } from "../components/EdgeByStrategyChart";
import { SymbolSignalOverlayChart } from "../components/SymbolSignalOverlayChart";
import { PilotsTableWidget } from "../components/PilotsTableWidget";
import { SentimentMiniChart } from "../components/SentimentMiniChart";
import { PortfolioHeatWidget } from "../components/PortfolioHeatWidget";
import { OptionsDirectiveSummary } from "../components/OptionsDirectiveSummary";
import { SignalBreakdownMiniWidget } from "../components/SignalBreakdownMiniWidget";
import { MacroRegimeBanner } from "../components/MacroRegimeBanner";

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

const WIDGET_CATEGORIES = {
  "Visualizations & Charts": ["edgeByStrategy", "symbolOverlay", "sentimentMini", "signalBreakdown"] as const,
  "Portfolios & Holdings": ["pilotsTable", "portfolioHeat", "optionsDirective"] as const,
  "Metrics & Intelligence": ["macroRegime", "aiChat"] as const,
};

const TEMPLATES = [
  {
    name: "Risk Management View",
    widgets: {
      edgeByStrategy: false,
      symbolOverlay: false,
      aiChat: false,
      pilotsTable: false,
      sentimentMini: false,
      portfolioHeat: true,
      optionsDirective: true,
      signalBreakdown: false,
      macroRegime: true,
    },
    widgetOrder: ["macroRegime", "portfolioHeat", "optionsDirective"] as (keyof CustomViewWidgets)[],
  },
  {
    name: "Algorithmic Trading Desk",
    widgets: {
      edgeByStrategy: true,
      symbolOverlay: true,
      aiChat: true,
      pilotsTable: false,
      sentimentMini: false,
      portfolioHeat: false,
      optionsDirective: false,
      signalBreakdown: true,
      macroRegime: false,
    },
    widgetOrder: ["edgeByStrategy", "symbolOverlay", "signalBreakdown", "aiChat"] as (keyof CustomViewWidgets)[],
  },
  {
    name: "Sentiment Overview",
    widgets: {
      edgeByStrategy: false,
      symbolOverlay: false,
      aiChat: false,
      pilotsTable: false,
      sentimentMini: true,
      portfolioHeat: false,
      optionsDirective: false,
      signalBreakdown: true,
      macroRegime: true,
    },
    widgetOrder: ["macroRegime", "sentimentMini", "signalBreakdown"] as (keyof CustomViewWidgets)[],
  }
];

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


export function CreateDataApp() {
  const navigate = useNavigate();
  const { views, addOrUpdateView, removeView, importViews } = useCustomViews();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [name, setName] = useState("");
  const [widgets, setWidgets] = useState<CustomViewWidgets>(DEFAULT_WIDGETS);
  
  // masterOrder keeps all widgets ordered, active or not, so toggling remembers position.
  const [masterOrder, setMasterOrder] = useState<(keyof CustomViewWidgets)[]>(Object.keys(WIDGET_LABELS) as any);
  
  // widgetOrder is derived for rendering the Reorder list
  const widgetOrder = masterOrder.filter(k => widgets[k]);

  const [widgetConfigs, setWidgetConfigs] = useState<Record<string, any>>({});
  const [editingId, setEditingId] = useState<string | null>(null);
  const [livePreview, setLivePreview] = useState(false);
  
  // Modal state
  const [configModalWidget, setConfigModalWidget] = useState<keyof CustomViewWidgets | null>(null);
  const [tempConfig, setTempConfig] = useState<Record<string, any>>({});

  const trimmedName = name.trim();
  const anyWidgetSelected = Object.values(widgets).some(Boolean);
  const canCreate = trimmedName.length > 0 && anyWidgetSelected;

  const toggleWidget = (key: keyof CustomViewWidgets) => {
    setWidgets((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const handleReorder = (newActiveOrder: (keyof CustomViewWidgets)[]) => {
    // Merge new active order with inactive items (which stay at the end or preserve their relative order)
    const inactive = masterOrder.filter(k => !widgets[k]);
    setMasterOrder([...newActiveOrder, ...inactive]);
  };

  const applyTemplate = (template: typeof TEMPLATES[0]) => {
    setName(template.name);
    setWidgets(template.widgets as CustomViewWidgets);
    
    // Reconstruct masterOrder placing the template's order first
    const active = template.widgetOrder;
    const inactive = masterOrder.filter(k => !active.includes(k));
    setMasterOrder([...active, ...inactive]);
    
    setWidgetConfigs({});
  };

  const loadViewForEditing = (v: CustomView) => {
    setEditingId(v.id);
    setName(v.name);
    setWidgets(v.widgets);
    
    const active = v.widgetOrder || [];
    const inactive = masterOrder.filter(k => !active.includes(k));
    setMasterOrder([...active, ...inactive]);

    setWidgetConfigs(v.widgetConfigs || {});
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const duplicateView = (v: CustomView) => {
    const { view, persisted } = addOrUpdateView({
      name: `${v.name} - Copy`,
      widgets: v.widgets,
      widgetOrder: v.widgetOrder,
      widgetConfigs: v.widgetConfigs,
    });
    if (persisted) toast.success(`Duplicated to "${view.name}"`);
    else toast.error("Could not persist duplication permanently.");
  };

  const handleCreate = () => {
    if (!canCreate) return;
    const { view, persisted } = addOrUpdateView({ id: editingId || undefined, name: trimmedName, widgets, widgetOrder, widgetConfigs });
    if (persisted) {
      toast.success(`Saved "${view.name}" to the sidebar.`);
    } else {
      toast.error(`"${view.name}" is only available for this session -- your browser didn't allow it to be saved permanently.`);
    }
    setEditingId(null);
    setName("");
    setWidgets(DEFAULT_WIDGETS);
    setMasterOrder(Object.keys(WIDGET_LABELS) as any);
    setWidgetConfigs({});
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

  const handleExport = (v: CustomView) => {
    const data = JSON.stringify([v], null, 2);
    const blob = new Blob([data], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `stockpy-data-app-${v.slug}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (event) => {
      const text = event.target?.result as string;
      const { importedCount, persisted, error } = importViews(text);
      if (error) {
        toast.error(`Failed to import: ${error}`);
      } else {
        if (persisted) {
          toast.success(`Successfully imported ${importedCount} view(s).`);
        } else {
          toast.success(`Imported ${importedCount} view(s) for this session only.`);
        }
      }
      if (fileInputRef.current) fileInputRef.current.value = "";
    };
    reader.readAsText(file);
  };

  const saveConfig = () => {
    if (configModalWidget) {
      setWidgetConfigs(prev => ({ ...prev, [configModalWidget]: tempConfig }));
      setConfigModalWidget(null);
    }
  };

  return (
    <div className="screen">
      <h1 className="screen-title">Create Data App</h1>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-body)", marginTop: "var(--s-1)" }}>
        Name a custom view built from real, live widgets and save it as a working sidebar shortcut.
      </p>

      <TabGuide tabKey="create-data-app" />

      {/* Widget Configuration Modal (Simple Overlay) */}
      {configModalWidget && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 100,
          display: 'flex', alignItems: 'center', justifyContent: 'center'
        }}>
          <div className="card card-pad" style={{ width: 400, maxWidth: '90vw' }}>
            <h3 style={{ marginTop: 0 }}>Configure {WIDGET_LABELS[configModalWidget]}</h3>
            <div style={{ marginTop: "var(--s-3)", marginBottom: "var(--s-4)" }}>
              <Input
                label="Default Ticker (Optional)"
                value={tempConfig.defaultTicker || ""}
                onChange={(e) => setTempConfig({ ...tempConfig, defaultTicker: e.target.value })}
                placeholder="e.g. AAPL"
              />
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: "var(--s-2)" }}>
              <Button variant="neutral" onClick={() => setConfigModalWidget(null)}>Cancel</Button>
              <Button variant="primary" onClick={saveConfig}>Save</Button>
            </div>
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--s-4)", marginTop: "var(--s-4)", alignItems: "start" }}>
        
        {/* Left Column: Form & Selection */}
        <section className="card card-pad">
          <h2 style={{ fontSize: "var(--t-input)", margin: "0 0 var(--s-3)" }}>Editor</h2>

          <div style={{ display: 'flex', gap: "var(--s-2)", marginBottom: "var(--s-4)", flexWrap: 'wrap' }}>
            {TEMPLATES.map(t => (
              <Button key={t.name} variant="neutral" onClick={() => applyTemplate(t)} style={{ fontSize: "var(--t-caption)" }}>
                Use {t.name}
              </Button>
            ))}
          </div>

          <div style={{ marginBottom: "var(--s-3)" }}>
            <Input
              label="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Momentum Desk"
            />
          </div>

          <div style={{ marginBottom: "var(--s-4)" }}>
            <div className="tile-label" style={{ marginBottom: "var(--s-2)" }}>Widgets</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
              {Object.entries(WIDGET_CATEGORIES).map(([category, keys]) => (
                <div key={category}>
                  <div style={{ fontSize: "var(--t-caption)", fontWeight: 600, color: theme.textMuted, marginBottom: "var(--s-1-5)" }}>
                    {category}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)", paddingLeft: "var(--s-2)" }}>
                    {keys.map((key) => (
                      <label key={key} style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", fontSize: "var(--t-caption)", cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={widgets[key as keyof CustomViewWidgets]}
                          onChange={() => toggleWidget(key as keyof CustomViewWidgets)}
                          data-testid={`widget-toggle-${key}`}
                        />
                        {WIDGET_LABELS[key as keyof CustomViewWidgets]}
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            {!anyWidgetSelected && (
              <div style={{ marginTop: "var(--s-2)", fontSize: "var(--t-caption)", color: "var(--decline)" }}>
                Pick at least one widget.
              </div>
            )}
          </div>

          <Button variant="primary" disabled={!canCreate} onClick={handleCreate} data-testid="create-data-app-submit">
            {editingId ? "Update & save to sidebar" : "Create & save to sidebar"}
          </Button>
          {editingId && (
            <Button variant="neutral" onClick={() => {
              setEditingId(null);
              setName("");
              setWidgets(DEFAULT_WIDGETS);
              setMasterOrder(Object.keys(WIDGET_LABELS) as any);
              setWidgetConfigs({});
            }} style={{ marginLeft: "var(--s-2)" }}>
              Cancel Edit
            </Button>
          )}
        </section>

        {/* Right Column: Live Preview & Ordering */}
        <section className="card card-pad" style={{ minHeight: 400 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-3)" }}>
            <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Live Layout Preview</h2>
            <label style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", fontSize: "var(--t-caption)", cursor: "pointer" }}>
              <input type="checkbox" checked={livePreview} onChange={(e) => setLivePreview(e.target.checked)} />
              Preview live
            </label>
          </div>
          <p style={{ fontSize: "var(--t-caption)", color: theme.textMuted, marginBottom: "var(--s-3)" }}>
            Drag to reorder widgets. Configuration settings apply when saved.
          </p>
          
          {widgetOrder.length === 0 ? (
            <EmptyState title="No widgets selected" hint="Check a widget on the left to add it." />
          ) : (
            <Reorder.Group axis="y" values={widgetOrder} onReorder={handleReorder} style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
              {widgetOrder.map((key) => (
                <Reorder.Item key={key} value={key} style={{
                  background: theme.surface2,
                  border: `1px solid ${theme.border}`,
                  borderRadius: 8,
                  padding: "var(--s-2) var(--s-3)",
                  display: "flex",
                  flexDirection: "column",
                  cursor: "grab"
                }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: "var(--s-2)" }}>
                      <GripVertical size={16} color={theme.textMuted} />
                      <span style={{ fontSize: "var(--t-caption)", fontWeight: 500 }}>{WIDGET_LABELS[key]}</span>
                    </div>
                    {key === "symbolOverlay" && (
                      <Button variant="neutral" onClick={() => {
                        setTempConfig(widgetConfigs[key] || {});
                        setConfigModalWidget(key);
                      }} title="Configure Widget">
                        <Settings size={14} />
                      </Button>
                    )}
                  </div>
                  {livePreview && (
                    <div style={{ marginTop: "var(--s-3)", pointerEvents: "none", opacity: 0.8 }}>
                      {key === "edgeByStrategy" && <EdgeByStrategyChart {...(widgetConfigs[key] || {})} />}
                      {key === "symbolOverlay" && <SymbolSignalOverlayChart {...(widgetConfigs[key] || {})} />}
                      {key === "pilotsTable" && <PilotsTableWidget {...(widgetConfigs[key] || {})} />}
                      {key === "sentimentMini" && <SentimentMiniChart {...(widgetConfigs[key] || {})} />}
                      {key === "portfolioHeat" && <PortfolioHeatWidget {...(widgetConfigs[key] || {})} />}
                      {key === "optionsDirective" && <OptionsDirectiveSummary {...(widgetConfigs[key] || {})} />}
                      {key === "signalBreakdown" && <SignalBreakdownMiniWidget {...(widgetConfigs[key] || {})} />}
                      {key === "macroRegime" && <MacroRegimeBanner {...(widgetConfigs[key] || {})} />}
                      {key === "aiChat" && (
                        <div style={{ padding: "var(--s-3)", border: `1px solid ${theme.border}`, borderRadius: 8 }}>
                          <h3 style={{ margin: "0 0 var(--s-2)" }}>Ask AI</h3>
                          <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>[Chat Preview]</p>
                        </div>
                      )}
                    </div>
                  )}
                </Reorder.Item>
              ))}
            </Reorder.Group>
          )}
        </section>
      </div>

      {/* Management Section */}
      <div style={{ marginTop: "var(--s-4)" }}>
        <section className="card card-pad">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: "var(--s-3)" }}>
            <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Your Data Apps</h2>
            <div>
              <input type="file" accept=".json" style={{ display: 'none' }} ref={fileInputRef} onChange={handleImport} />
              <Button variant="neutral" onClick={() => fileInputRef.current?.click()}>
                <span aria-hidden style={{ display: "inline-flex", alignItems: "center", gap: "var(--s-1-5)" }}>
                  <Upload size={14} strokeWidth={2.5} /> Import JSON
                </span>
              </Button>
            </div>
          </div>
          
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
                        {v.widgetOrder.map((k) => WIDGET_LABELS[k]).join(" · ") || "No widgets"}
                      </div>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "var(--s-2)", flexShrink: 0 }}>
                    <Button variant="neutral" onClick={() => navigate(`/app/${v.slug}`)} data-testid={`data-app-open-${v.slug}`} title="Open View">
                      Open
                    </Button>
                    <Button variant="neutral" onClick={() => loadViewForEditing(v)} title="Edit View">
                      <span aria-hidden style={{ display: "inline-flex", alignItems: "center" }}><Edit2 size={16} strokeWidth={2.5} /></span>
                    </Button>
                    <Button variant="neutral" onClick={() => duplicateView(v)} title="Duplicate View">
                      <span aria-hidden style={{ display: "inline-flex", alignItems: "center" }}><Copy size={16} strokeWidth={2.5} /></span>
                    </Button>
                    <Button variant="neutral" onClick={() => handleExport(v)} title="Export View as JSON">
                      <span aria-hidden style={{ display: "inline-flex", alignItems: "center" }}><Download size={16} strokeWidth={2.5} /></span>
                    </Button>
                    <Button
                      variant="neutral"
                      onClick={() => handleDelete(v.id, v.name)}
                      data-testid={`data-app-delete-${v.slug}`}
                      aria-label={`Delete ${v.name}`}
                      title="Delete View"
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
