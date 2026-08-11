import React, { useState, useRef, useEffect } from "react";
import { LayoutTemplate } from "lucide-react";
import { addDynamicNavItem } from "../navigation";
import { theme } from "../theme";
import { TabGuide } from "../components/TabGuide";
import { api } from "../api/client";
import toast from "react-hot-toast";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ComposedChart,
  BarChart,
  Bar,
  Scatter,
  Legend
} from "recharts";
import {
  flexRender,
  useTable,
  tableFeatures,
  rowExpandingFeature,
  createExpandedRowModel
} from "@tanstack/react-table";
import { EdgeByStrategyRow, PilotSummary, Holding, ObservabilitySummary, SentimentHistoryPoint } from "../api/types";

function ExpandedHoldings({ pilotId }: { pilotId: string }) {
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getHoldings(pilotId).then(data => {
      setHoldings(data);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [pilotId]);

  if (loading) return <div style={{ padding: 16 }}>Loading holdings...</div>;
  if (!holdings.length) return <div style={{ padding: 16 }}>No holdings found.</div>;

  return (
    <div style={{ padding: 16, background: theme.surface2 }}>
      <h4 style={{ margin: "0 0 8px" }}>Holdings</h4>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        {holdings.map(h => (
          <div key={h.symbol} style={{ border: `1px solid ${theme.border}`, padding: 8, borderRadius: 4 }}>
            <strong>{h.symbol}</strong>: {h.weight != null ? (h.weight * 100).toFixed(1) : 0}%
          </div>
        ))}
      </div>
    </div>
  );
}

export function CreateDataApp() {
  const [appName, setAppName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  
  // Chat state
  const [query, setQuery] = useState("");
  const [chatHistory, setChatHistory] = useState<{role: "user"|"assistant", content: string, thoughts?: string[]}[]>([]);
  const [isTyping, setIsTyping] = useState(false);
  const [currentThought, setCurrentThought] = useState("");
  
  const bottomRef = useRef<HTMLDivElement>(null);

  // New data states
  const [edgeData, setEdgeData] = useState<EdgeByStrategyRow[]>([]);
  const [priceHistory, setPriceHistory] = useState<{date: string; price: number | null; buyAction?: number; sellAction?: number}[]>([]);
  const [pilots, setPilots] = useState<PilotSummary[]>([]);
  const [obsSummary, setObsSummary] = useState<ObservabilitySummary | null>(null);
  const [selectedPilot, setSelectedPilot] = useState<PilotSummary | null>(null);
  const [sentimentData, setSentimentData] = useState<SentimentHistoryPoint[]>([]);

  useEffect(() => {
    async function loadData() {
      try {
        const edgeRes = await api.getEdgeByStrategy();
        setEdgeData(edgeRes.rows || []);
        
        const pilotsRes = await api.listPilots();
        setPilots(pilotsRes);
        
        const obsRes = await api.getObservabilitySummary("1Y", 30);
        setObsSummary(obsRes);
        
        const bars = await api.getDataBars("AAPL", 252);
        const decisions = await api.getDecisions({ symbol: "AAPL" });
        
        const merged = bars.map(b => {
          const dt = b.date.split("T")[0];
          const matchingDecision = decisions.find(d => (d.timestamp || d.signal_ts || "").startsWith(dt));
          const action = matchingDecision?.action_taken || matchingDecision?.signal_action;
          
          return {
            date: dt,
            price: b.Close,
            buyAction: action === "BUY" && b.Close != null ? b.Close : undefined,
            sellAction: action === "SELL" && b.Close != null ? b.Close : undefined,
          };
        });
        setPriceHistory(merged);
        
        const sentimentRes = await api.getSentimentHistory("AAPL", 180);
        setSentimentData(sentimentRes.points || []);
        
      } catch(e) {
        console.error("Failed to load charts data", e);
      }
    }
    loadData();
  }, []);

  const columns: any[] = [
    {
      id: "expander",
      header: () => null,
      cell: ({ row }: any) => {
        return (
          <button
            {...{
              onClick: row.getToggleExpandedHandler(),
              style: { cursor: 'pointer', background: 'transparent', border: 'none', color: theme.textPrimary },
            }}
          >
            {row.getIsExpanded() ? '👇' : '👉'}
          </button>
        )
      },
    },
    {
      accessorKey: "name",
      header: "Strategy",
    },
    {
      accessorKey: "category",
      header: "Category",
    },
    {
      accessorKey: "holdings_count",
      header: "Holdings",
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }: any) => {
        return (
          <button
            onClick={() => setSelectedPilot(row.original)}
            style={{
              padding: "4px 8px",
              borderRadius: 4,
              background: theme.accent,
              color: theme.surface,
              border: "none",
              cursor: "pointer",
            }}
          >
            Simulate
          </button>
        );
      },
    },
  ];

  const features = tableFeatures({
    rowExpandingFeature,
    expandedRowModel: createExpandedRowModel(),
  });

  const table = useTable({
    features,
    data: pilots,
    columns,
    getRowCanExpand: () => true,
  } as any);

  useEffect(() => {
    // Optional-chain the METHOD itself, not just the ref: jsdom (the test
    // environment) renders a real element but doesn't implement
    // scrollIntoView, so `bottomRef.current?.scrollIntoView(...)` throws
    // "not a function" under vitest even though the ref is non-null.
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [chatHistory, currentThought]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!appName.trim()) return;
    
    setIsSubmitting(true);
    try {
      await api.createDataApp({ name: appName });
      setSuccess(true);
      toast.success("App created successfully!");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create app");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSaveToDashboard = async () => {
    if (!appName.trim()) {
      toast.error("Please enter an app name first");
      return;
    }
    setIsSubmitting(true);
    try {
      const res = await api.saveDataApp({ name: appName });
      toast.success("App saved to dashboard!");
      
      const slug = "/app/" + encodeURIComponent(res.saved_app.toLowerCase().replace(/\s+/g, '-'));
      addDynamicNavItem({
        to: slug,
        label: res.saved_app,
        ico: LayoutTemplate,
        match: (p) => p.startsWith(slug),
        section: "operations"
      });
      setSuccess(true);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save app");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleChat = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    
    const userMsg = query;
    setQuery("");
    setChatHistory(prev => [...prev, { role: "user", content: userMsg }]);
    setIsTyping(true);
    setCurrentThought("");
    
    const assistantIndex = chatHistory.length + 1;
    setChatHistory(prev => [...prev, { role: "assistant", content: "", thoughts: [] }]);
    
    try {
      // Simulate SSE connection for mock
      const es = new EventSource(`http://localhost:8602/chat/stream?query=${encodeURIComponent(userMsg)}`);
      
      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          const msg = data.system_message;
          if (msg.text_type === "THOUGHT") {
            setCurrentThought(msg.text);
            setChatHistory(prev => {
              const next = [...prev];
              next[assistantIndex].thoughts?.push(msg.text);
              return next;
            });
          } else if (msg.text_type === "FINAL_RESPONSE") {
            setChatHistory(prev => {
              const next = [...prev];
              next[assistantIndex].content += msg.text;
              return next;
            });
          }
        } catch (e) {
          console.error("SSE parse error", e);
        }
      };
      
      es.onerror = () => {
        es.close();
        setIsTyping(false);
        setCurrentThought("");
      };
      
    } catch (err) {
      toast.error("Chat failed");
      setIsTyping(false);
    }
  };

  return (
    <div className="screen-container">
      <div className="screen-header">
        <h1 style={{ margin: "0 0 4px", fontSize: "var(--t-title)" }}>Create Data App</h1>
        <div style={{ color: theme.textSecondary, fontSize: 15 }}>
          Build interactive data applications.
        </div>
      </div>
      <TabGuide tabKey="create-data-app" />
      <div className="screen-content" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 32 }}>
        <div style={{ display: 'flex', gap: 24 }}>
        {/* Left Column: Form */}
        <div style={{ flex: 1 }}>
          <h2 style={{ fontSize: 18, marginBottom: 16 }}>Configuration</h2>
          {success ? (
            <div style={{ color: theme.growth }}>
              <h3>Data App Created</h3>
              <p>Your application has been provisioned.</p>
            </div>
          ) : (
            <form onSubmit={handleSubmit}>
              <div style={{ marginBottom: 16 }}>
                <label htmlFor="appName" style={{ display: "block", marginBottom: 8, color: theme.textSecondary }}>
                  App Name
                </label>
                <input
                  id="appName"
                  type="text"
                  value={appName}
                  onChange={(e) => setAppName(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    borderRadius: 6,
                    border: `1px solid ${theme.border}`,
                    background: theme.surface,
                    color: theme.textPrimary,
                  }}
                />
              </div>
              <div style={{ display: "flex", gap: 12 }}>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 6,
                    background: theme.base,
                    color: theme.surface,
                    border: "none",
                    cursor: isSubmitting ? "not-allowed" : "pointer",
                    fontWeight: 600,
                  }}
                >
                  Create App
                </button>
                <button
                  type="button"
                  onClick={handleSaveToDashboard}
                  disabled={isSubmitting}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 6,
                    background: theme.accent,
                    color: theme.surface,
                    border: "none",
                    cursor: isSubmitting ? "not-allowed" : "pointer",
                    fontWeight: 600,
                  }}
                >
                  Save to Dashboard
                </button>
              </div>
            </form>
          )}
        </div>
        
        {/* Right Column: Chat */}
        <div style={{ flex: 1, border: `1px solid ${theme.border}`, borderRadius: 8, display: 'flex', flexDirection: 'column', height: '600px', background: theme.surface }}>
          <div style={{ padding: 16, borderBottom: `1px solid ${theme.border}`, fontWeight: 600 }}>
            Data Assistant
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
            {chatHistory.map((msg, idx) => (
              <div key={idx} style={{ 
                alignSelf: msg.role === "user" ? "flex-end" : "flex-start",
                background: msg.role === "user" ? theme.base : theme.surface2,
                color: msg.role === "user" ? theme.surface : theme.textPrimary,
                padding: "8px 12px",
                borderRadius: 8,
                maxWidth: "80%"
              }}>
                <div className="[&>p]:mb-2 [&>p:last-child]:mb-0">
                  <ReactMarkdown 
                    remarkPlugins={[remarkGfm]}
                    components={{
                      button: ({node, ...props}: any) => {
                        const isFollowAction = typeof props.children === 'string' && props.children.startsWith("Follow:");
                        if (isFollowAction) {
                          const pilotId = props.children.split(":")[1].trim();
                          return (
                            <button
                              onClick={() => {
                                api.followPilot(pilotId, 100).then(() => {
                                  toast.success(`Successfully followed ${pilotId}`);
                                }).catch(err => {
                                  toast.error(err instanceof Error ? err.message : "Follow failed");
                                });
                              }}
                              style={{
                                padding: "4px 8px",
                                borderRadius: 4,
                                background: theme.accent,
                                color: theme.surface,
                                border: "none",
                                cursor: "pointer",
                                marginTop: 8
                              }}
                            >
                              Follow {pilotId}
                            </button>
                          );
                        }
                        return <button {...props} />;
                      }
                    }}
                  >{msg.content}</ReactMarkdown>
                </div>
              </div>
            ))}
            {isTyping && currentThought && (
              <div style={{ color: theme.textSecondary, fontSize: 13, fontStyle: 'italic' }}>
                {currentThought}
              </div>
            )}
            <div ref={bottomRef} />
          </div>
          <form onSubmit={handleChat} style={{ padding: 16, borderTop: `1px solid ${theme.border}`, display: 'flex', gap: 8 }}>
            <input
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Ask about your data..."
              style={{
                flex: 1,
                padding: "8px 12px",
                borderRadius: 6,
                border: `1px solid ${theme.border}`,
                background: theme.surface2,
                color: theme.textPrimary,
              }}
            />
            <button
              type="submit"
              disabled={isTyping || !query.trim()}
              style={{
                padding: "8px 16px",
                borderRadius: 6,
                background: theme.base,
                color: theme.surface,
                border: "none",
                cursor: (isTyping || !query.trim()) ? "not-allowed" : "pointer",
                fontWeight: 600,
              }}
            >
              Send
            </button>
          </form>
        </div>
        </div>

        {/* Bottom row: Data Visualizations */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
          {/* Top of Bottom Row: Two charts side-by-side */}
          <div style={{ display: 'flex', gap: 24 }}>
            {/* Chart 1 container */}
            <div style={{ flex: 1, background: theme.surface, padding: 16, borderRadius: 8, border: `1px solid ${theme.border}` }}>
              <h3 style={{ marginTop: 0, marginBottom: 16, fontSize: 16 }}>Edge per Strategy</h3>
              <div style={{ width: '100%', height: 300 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={edgeData}>
                    <CartesianGrid strokeDasharray="3 3" stroke={theme.border} />
                    <XAxis dataKey="strategy" stroke={theme.textSecondary} fontSize={12} />
                    <YAxis yAxisId="left" orientation="left" stroke={theme.textSecondary} />
                    <YAxis yAxisId="right" orientation="right" stroke={theme.textSecondary} />
                    <Tooltip 
                      contentStyle={{ backgroundColor: theme.surface, borderColor: theme.border, color: theme.textPrimary }} 
                      itemStyle={{ color: theme.textPrimary }}
                    />
                    <Legend />
                    <Bar yAxisId="left" dataKey="mean_edge_ratio" fill={theme.accent} name="Mean Edge Ratio" />
                    <Bar yAxisId="right" dataKey="n_trades" fill={theme.growth} name="Trades" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Chart 2 container */}
            <div style={{ flex: 1, background: theme.surface, padding: 16, borderRadius: 8, border: `1px solid ${theme.border}` }}>
              <h3 style={{ marginTop: 0, marginBottom: 16, fontSize: 16 }}>Symbol Price History (AAPL)</h3>
              <div style={{ width: '100%', height: 300 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={priceHistory}>
                    <CartesianGrid strokeDasharray="3 3" stroke={theme.border} />
                    <XAxis dataKey="date" stroke={theme.textSecondary} fontSize={12} tickFormatter={(val) => val.split('-').slice(1).join('/')} />
                    <YAxis stroke={theme.textSecondary} domain={['auto', 'auto']} />
                    <Tooltip 
                      contentStyle={{ backgroundColor: theme.surface, borderColor: theme.border, color: theme.textPrimary }} 
                      itemStyle={{ color: theme.textPrimary }}
                    />
                    <Legend />
                    <Line type="monotone" dataKey="price" stroke={theme.textPrimary} dot={false} strokeWidth={2} name="Price" />
                    <Scatter name="BUY" dataKey="buyAction" fill={theme.growth} shape="triangle" />
                    <Scatter name="SELL" dataKey="sellAction" fill={theme.accent} shape="cross" />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
          
          {/* Sentiment Backfill History */}
          <div style={{ background: theme.surface, padding: 16, borderRadius: 8, border: `1px solid ${theme.border}` }}>
            <h3 style={{ marginTop: 0, marginBottom: 16, fontSize: 16 }}>Sentiment Dynamics (AAPL) — Backfilled Archive</h3>
            <div style={{ width: '100%', height: 250 }}>
              {sentimentData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={sentimentData}>
                    <CartesianGrid strokeDasharray="3 3" stroke={theme.border} />
                    <XAxis 
                      dataKey="date" 
                      stroke={theme.textSecondary} 
                      fontSize={12} 
                      tickFormatter={(val) => val.split('-').slice(1).join('/')} 
                    />
                    <YAxis 
                      stroke={theme.textSecondary} 
                      domain={[-1, 1]} 
                    />
                    <Tooltip 
                      contentStyle={{ backgroundColor: theme.surface, borderColor: theme.border, color: theme.textPrimary }} 
                      itemStyle={{ color: theme.textPrimary }}
                      formatter={(val: any) => (typeof val === "number" ? [val.toFixed(2), "Sentiment Score"] : ["—", "Sentiment Score"])}
                    />
                    <Legend />
                    <Line 
                      type="monotone" 
                      dataKey="score" 
                      stroke={theme.accent} 
                      dot={false} 
                      strokeWidth={2} 
                      name="Sentiment Score (-1 to 1)" 
                      connectNulls={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              ) : (
                <div style={{ display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center', color: theme.textSecondary }}>
                  No backfill data available for AAPL.
                </div>
              )}
            </div>
          </div>

          {/* Table container */}
          <div style={{ background: theme.surface, padding: 16, borderRadius: 8, border: `1px solid ${theme.border}` }}>
            <h3 style={{ marginTop: 0, marginBottom: 16, fontSize: 16 }}>Available Strategies</h3>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
              <thead>
                {(table as any).getHeaderGroups().map((headerGroup: any) => (
                  <tr key={headerGroup.id} style={{ borderBottom: `1px solid ${theme.border}` }}>
                    {headerGroup.headers.map((header: any) => (
                      <th key={header.id} style={{ padding: '8px 0', color: theme.textSecondary, fontWeight: 500 }}>
                        {header.isPlaceholder
                          ? null
                          : flexRender(
                              header.column.columnDef.header,
                              header.getContext()
                            )}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {(table as any).getRowModel().rows.map((row: any) => (
                  <React.Fragment key={row.id}>
                    <tr style={{ borderBottom: `1px solid ${theme.surface2}` }}>
                      {row.getVisibleCells().map((cell: any) => (
                        <td key={cell.id} style={{ padding: '12px 0', color: theme.textPrimary }}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                    {row.getIsExpanded() && (
                      <tr>
                        <td colSpan={row.getVisibleCells().length}>
                          <ExpandedHoldings pilotId={row.original.id} />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>

          {/* Portfolio Risk/Heat section. NOTE: this shows CURRENT portfolio
              metrics only -- there is no backend endpoint yet that computes
              a real projected impact of following a given pilot's signals
              (that needs the pilot's own historical return series blended
              against current holdings, not a client-side guess). Per
              CONSTRAINT #4, selecting a strategy below highlights it for
              context but never fabricates a "projected" delta. */}
          {obsSummary && (
            <div style={{ background: theme.surface, padding: 24, borderRadius: 8, border: `1px solid ${theme.border}` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
                <h3 style={{ margin: 0, fontSize: 18 }}>Current Portfolio Risk & Heat</h3>
                {selectedPilot && (
                  <div style={{ padding: "4px 12px", background: theme.accent, color: theme.surface, borderRadius: 16, fontSize: 13, fontWeight: 600 }}>
                    Selected: {selectedPilot.name}
                  </div>
                )}
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 24 }}>
                {/* Sharpe Ratio */}
                <div style={{ padding: 20, borderRadius: 8, background: theme.surface2, border: `1px solid ${theme.border}` }}>
                  <div style={{ color: theme.textSecondary, fontSize: 14, marginBottom: 12 }}>Sharpe Ratio</div>
                  <span style={{ fontSize: 32, fontWeight: 700 }}>
                    {obsSummary.portfolio_risk.sharpe_ratio?.toFixed(2) ?? "—"}
                  </span>
                </div>

                {/* Max Drawdown */}
                <div style={{ padding: 20, borderRadius: 8, background: theme.surface2, border: `1px solid ${theme.border}` }}>
                  <div style={{ color: theme.textSecondary, fontSize: 14, marginBottom: 12 }}>Max Drawdown</div>
                  <span style={{ fontSize: 32, fontWeight: 700 }}>
                    {obsSummary.portfolio_risk.max_drawdown != null
                      ? `${(obsSummary.portfolio_risk.max_drawdown * 100).toFixed(1)}%`
                      : "—"}
                  </span>
                </div>

                {/* Portfolio Heat */}
                <div style={{ padding: 20, borderRadius: 8, background: theme.surface2, border: `1px solid ${theme.border}` }}>
                  <div style={{ color: theme.textSecondary, fontSize: 14, marginBottom: 12 }}>Portfolio Heat</div>
                  <span style={{ fontSize: 32, fontWeight: 700 }}>
                    {obsSummary.portfolio_heat.heat_pct != null
                      ? `${(obsSummary.portfolio_heat.heat_pct * 100).toFixed(1)}%`
                      : "—"}
                  </span>
                  <div style={{ marginTop: 16, height: 4, background: theme.border, borderRadius: 2, overflow: "hidden" }}>
                    {obsSummary.portfolio_heat.heat_pct != null && (
                      <div style={{ width: `${Math.min(100, obsSummary.portfolio_heat.heat_pct * 100)}%`, height: "100%", background: theme.base }} />
                    )}
                  </div>
                </div>
              </div>

              {!selectedPilot && (
                <div style={{ marginTop: 24, padding: 16, background: theme.surface2, borderRadius: 8, color: theme.textSecondary, textAlign: "center" }}>
                  Select a strategy from the table above for context on which pilot you're reviewing alongside your current portfolio metrics.
                </div>
              )}
              {selectedPilot && (
                <div style={{ marginTop: 24, padding: 16, background: theme.surface2, borderRadius: 8, color: theme.textSecondary, fontSize: 13, textAlign: "center" }}>
                  Projected portfolio impact from following {selectedPilot.name}'s signals isn't computed yet — this section shows your current portfolio's real metrics only.
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
