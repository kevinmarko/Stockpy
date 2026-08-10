import { useState, useRef, useEffect } from "react";
import { theme } from "../theme";
import { TabGuide } from "../components/TabGuide";
import { api } from "../api/client";
import toast from "react-hot-toast";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import {
  flexRender,
  useTable,
} from "@tanstack/react-table";

const chartData = [
  { name: "Jan", revenue: 4000, users: 2400 },
  { name: "Feb", revenue: 3000, users: 1398 },
  { name: "Mar", revenue: 2000, users: 9800 },
  { name: "Apr", revenue: 2780, users: 3908 },
  { name: "May", revenue: 1890, users: 4800 },
  { name: "Jun", revenue: 2390, users: 3800 },
];

type MetricRow = {
  metric: string;
  value: number;
  growth: string;
};

const tableData: MetricRow[] = [
  { metric: "Total Revenue", value: 16060, growth: "+12%" },
  { metric: "Active Users", value: 24206, growth: "+5%" },
  { metric: "Avg Session", value: 120, growth: "-2%" },
];

const columns: any[] = [
  {
    accessorKey: "metric",
    header: "Metric",
    cell: (info: any) => info.getValue(),
  },
  {
    accessorKey: "value",
    header: "Value",
    cell: (info: any) => (info.getValue() as number).toLocaleString(),
  },
  {
    accessorKey: "growth",
    header: "Growth",
    cell: (info: any) => info.getValue(),
  },
];

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

  const table = useTable({
    data: tableData,
    columns,
  } as any);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
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
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
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
        <div style={{ display: 'flex', gap: 24 }}>
          {/* Chart container */}
          <div style={{ flex: 1, background: theme.surface, padding: 16, borderRadius: 8, border: `1px solid ${theme.border}` }}>
            <h3 style={{ marginTop: 0, marginBottom: 16, fontSize: 16 }}>Revenue Overview</h3>
            <div style={{ width: '100%', height: 300 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.border} />
                  <XAxis dataKey="name" stroke={theme.textSecondary} />
                  <YAxis stroke={theme.textSecondary} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: theme.surface, borderColor: theme.border, color: theme.textPrimary }} 
                    itemStyle={{ color: theme.textPrimary }}
                  />
                  <Line type="monotone" dataKey="revenue" stroke={theme.accent} strokeWidth={2} />
                  <Line type="monotone" dataKey="users" stroke={theme.growth} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Table container */}
          <div style={{ flex: 1, background: theme.surface, padding: 16, borderRadius: 8, border: `1px solid ${theme.border}` }}>
            <h3 style={{ marginTop: 0, marginBottom: 16, fontSize: 16 }}>Key Metrics</h3>
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
                  <tr key={row.id} style={{ borderBottom: `1px solid ${theme.surface2}` }}>
                    {row.getVisibleCells().map((cell: any) => (
                      <td key={cell.id} style={{ padding: '12px 0', color: theme.textPrimary }}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
