import { useState, useRef } from "react";
import { useNavigate } from "react-router";
import toast from "react-hot-toast";
import { LayoutTemplate, Trash2, Edit2, Copy, Download, Upload, GripVertical, Settings, ArrowUp, ArrowDown } from "lucide-react";
import { Reorder } from "framer-motion";
import { Button, EmptyState, Input } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { useCustomViews, slugify, getViewById, type CustomViewWidgets, type CustomView } from "../customViews";
import { WIDGET_LABELS, ALL_WIDGET_KEYS, WIDGET_COMPONENTS, type NonChatWidgetKey } from "../widgetRegistry";
import { theme } from "../theme";

/** Cosmetic-only id fragment for template buttons' `data-testid` -- NOT the
 * same as customViews.ts's `slugify` (that one governs the real, persisted
 * `/app/:slug` route and has its own collision-avoidance rules; this one
 * only needs to be stable and readable for tests). */
function slugifyForTestId(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

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


/**
 * Renders one widget's live-preview content in the right-column layout
 * preview. `aiChat` is intentionally not in `WIDGET_COMPONENTS` -- it needs
 * the real `useChat()` context, so this preview shows a static, non-
 * interactive placeholder instead of the real chat button `CustomView.tsx`
 * renders. See widgetRegistry.tsx's module doc for the full reasoning.
 */
function renderWidgetPreview(key: keyof CustomViewWidgets, config: any) {
  if (key === "aiChat") {
    return (
      <div style={{ padding: "var(--s-3)", border: `1px solid ${theme.border}`, borderRadius: 8 }}>
        <h3 style={{ margin: "0 0 var(--s-2)" }}>Ask AI</h3>
        <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>[Chat Preview]</p>
      </div>
    );
  }
  const entry = WIDGET_COMPONENTS[key as NonChatWidgetKey];
  if (!entry) return null;
  const { Component } = entry;
  return <Component {...config} />;
}

export function CreateDataApp() {
  const navigate = useNavigate();
  const { views, addOrUpdateView, removeView, importViews } = useCustomViews();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [name, setName] = useState("");
  const [widgets, setWidgets] = useState<CustomViewWidgets>(DEFAULT_WIDGETS);
  
  // masterOrder keeps all widgets ordered, active or not, so toggling remembers position.
  const [masterOrder, setMasterOrder] = useState<(keyof CustomViewWidgets)[]>(ALL_WIDGET_KEYS);
  
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

  // Keyboard/pointer-accessible alternative to the drag handle -- framer-motion's
  // Reorder.Group has no keyboard interaction of its own, which would otherwise
  // make widget ordering entirely unreachable without a mouse/touch drag.
  const moveWidget = (key: keyof CustomViewWidgets, direction: -1 | 1) => {
    const idx = widgetOrder.indexOf(key);
    const nextIdx = idx + direction;
    if (idx < 0 || nextIdx < 0 || nextIdx >= widgetOrder.length) return;
    const next = [...widgetOrder];
    [next[idx], next[nextIdx]] = [next[nextIdx], next[idx]];
    handleReorder(next);
  };

  const applyTemplate = (template: typeof TEMPLATES[0]) => {
    // Applying a template always starts a fresh view -- if the operator was
    // mid-edit of an existing saved view, silently keeping `editingId` set
    // would turn "Use Template" into "overwrite the view I was editing with
    // this template" on the next Save, which is not what the button says it
    // does. Discard the in-progress edit explicitly instead.
    setEditingId(null);
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

  /**
   * A naive constant `${v.name} - Copy` collides with itself on a second
   * click: storage keys off `slugify(name)`, so `addOrUpdateView`'s own
   * "no id given -> find by slug" rule treats the second call as an UPDATE
   * of the first duplicate rather than a new, third view (the second click
   * silently overwrote the first copy instead of producing a "- Copy 2").
   * Mirrors `addOrUpdateView`'s own rename-collision check (find an existing
   * view whose slug matches the candidate) rather than inventing a new
   * collision rule -- keep incrementing the suffix until a free slug is
   * found.
   */
  const duplicateView = (v: CustomView) => {
    let candidateName = `${v.name} - Copy`;
    let suffix = 2;
    while (views.some((existing) => slugify(existing.name) === slugify(candidateName))) {
      candidateName = `${v.name} - Copy ${suffix}`;
      suffix++;
    }

    const { view, persisted } = addOrUpdateView({
      name: candidateName,
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
    setMasterOrder(ALL_WIDGET_KEYS);
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

    // Snapshot the currently-edited view's `updatedAt` (if any) BEFORE the
    // import runs, so we can tell afterward whether the import actually
    // touched it -- `getViewById` reads live module state, not this
    // component's stale render-time `views`.
    const editingBefore = editingId ? getViewById(editingId) : undefined;

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

        // If the view currently open in the editor was one of the ones this
        // import just overwrote (`importViews` preserves `id` and stamps a
        // fresh `updatedAt` for a slug match), the in-form state is now
        // stale -- a subsequent Save would silently clobber the import that
        // just landed. Refresh the form from the freshly-imported version
        // instead of leaving that trap in place.
        if (editingBefore) {
          const refreshed = getViewById(editingBefore.id);
          if (refreshed && refreshed.updatedAt !== editingBefore.updatedAt) {
            loadViewForEditing(refreshed);
            toast(`"${refreshed.name}" was just updated by this import -- the editor was refreshed to match.`, { icon: "ℹ️" });
          }
        }
      }
      if (fileInputRef.current) fileInputRef.current.value = "";
    };
    reader.onerror = () => {
      toast.error("Failed to read the file. Please try again.");
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
              <Button
                key={t.name}
                variant="neutral"
                onClick={() => applyTemplate(t)}
                style={{ fontSize: "var(--t-caption)" }}
                data-testid={`use-template-${slugifyForTestId(t.name)}`}
              >
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
              setMasterOrder(ALL_WIDGET_KEYS);
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
            Drag to reorder widgets, or use the arrow buttons.
          </p>

          {widgetOrder.length === 0 ? (
            <EmptyState title="No widgets selected" hint="Check a widget on the left to add it." />
          ) : (
            <Reorder.Group axis="y" values={widgetOrder} onReorder={handleReorder} style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
              {widgetOrder.map((key, idx) => (
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
                      <GripVertical size={16} color={theme.textMuted} aria-hidden />
                      <span style={{ fontSize: "var(--t-caption)", fontWeight: 500 }}>{WIDGET_LABELS[key]}</span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1)" }}>
                      {/* Keyboard/pointer-accessible alternative to the drag
                          handle above -- framer-motion's Reorder.Group has no
                          keyboard interaction of its own. */}
                      <Button
                        variant="neutral"
                        onClick={() => moveWidget(key, -1)}
                        disabled={idx === 0}
                        aria-label={`Move ${WIDGET_LABELS[key]} up`}
                        data-testid={`widget-move-up-${key}`}
                      >
                        <ArrowUp size={14} />
                      </Button>
                      <Button
                        variant="neutral"
                        onClick={() => moveWidget(key, 1)}
                        disabled={idx === widgetOrder.length - 1}
                        aria-label={`Move ${WIDGET_LABELS[key]} down`}
                        data-testid={`widget-move-down-${key}`}
                      >
                        <ArrowDown size={14} />
                      </Button>
                      {key === "symbolOverlay" && (
                        <Button variant="neutral" onClick={() => {
                          setTempConfig(widgetConfigs[key] || {});
                          setConfigModalWidget(key);
                        }} title="Configure widget" aria-label={`Configure ${WIDGET_LABELS[key]}`}>
                          <Settings size={14} />
                        </Button>
                      )}
                    </div>
                  </div>
                  {livePreview && (
                    <div style={{ marginTop: "var(--s-3)", pointerEvents: "none", opacity: 0.8 }}>
                      {renderWidgetPreview(key, widgetConfigs[key] || {})}
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
              <input
                type="file"
                accept=".json"
                style={{ display: 'none' }}
                ref={fileInputRef}
                onChange={handleImport}
                data-testid="data-app-import-file-input"
              />
              <Button variant="neutral" onClick={() => fileInputRef.current?.click()} data-testid="data-app-import-trigger">
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
                    <Button
                      variant="neutral"
                      onClick={() => loadViewForEditing(v)}
                      title="Edit view"
                      aria-label={`Edit ${v.name}`}
                      data-testid={`data-app-edit-${v.slug}`}
                    >
                      <span aria-hidden style={{ display: "inline-flex", alignItems: "center" }}><Edit2 size={16} strokeWidth={2.5} /></span>
                    </Button>
                    <Button
                      variant="neutral"
                      onClick={() => duplicateView(v)}
                      title="Duplicate view"
                      aria-label={`Duplicate ${v.name}`}
                      data-testid={`data-app-duplicate-${v.slug}`}
                    >
                      <span aria-hidden style={{ display: "inline-flex", alignItems: "center" }}><Copy size={16} strokeWidth={2.5} /></span>
                    </Button>
                    <Button
                      variant="neutral"
                      onClick={() => handleExport(v)}
                      title="Export view as JSON"
                      aria-label={`Export ${v.name} as JSON`}
                      data-testid={`data-app-export-${v.slug}`}
                    >
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
