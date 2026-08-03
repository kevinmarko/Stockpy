import React, { useState } from "react";
import { useSearchParams } from "react-router";
import { api, ApiError } from "../api/client";
import type { MultiAgentResponse } from "../api/types";
import { PageHeader } from "../components/PageHeader";
import { BrainCircuit, Search, AlertCircle, CheckCircle2, ChevronRight, Activity, MessageSquare } from "lucide-react";

export const MultiAgentAnalysis: React.FC = () => {
  const [searchParams] = useSearchParams();
  const initialSymbols = searchParams.get("symbols") || "";
  const [symbols, setSymbols] = useState(initialSymbols);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<MultiAgentResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleAnalyze = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!symbols.trim()) return;

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const tickerList = symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
      const res = await api.analyzeAgents({ symbols: tickerList, query: query.trim() || undefined });
      setResult(res);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("An unexpected error occurred.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto px-4 py-8">
      <PageHeader
        title="Multi-Agent Analysis"
        description="Run deep research and execution planning via autonomous AI agents."
        icon={BrainCircuit}
      />

      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="p-6">
          <form onSubmit={handleAnalyze} className="space-y-4">
            <div>
              <label htmlFor="symbols" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Tickers (comma separated)
              </label>
              <input
                id="symbols"
                type="text"
                placeholder="e.g. AAPL, MSFT"
                value={symbols}
                onChange={(e) => setSymbols(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-indigo-500 dark:bg-gray-700 dark:text-white"
                required
              />
            </div>
            <div>
              <label htmlFor="query" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Specific Research Query (Optional)
              </label>
              <textarea
                id="query"
                placeholder="e.g. Focus on AI revenue growth vs competitors"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-indigo-500 dark:bg-gray-700 dark:text-white h-24 resize-none"
              />
            </div>
            <div className="flex justify-end">
              <button
                type="submit"
                disabled={loading || !symbols.trim()}
                className="inline-flex items-center px-6 py-2 border border-transparent text-sm font-medium rounded-lg shadow-sm text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <>
                    <Activity className="animate-spin -ml-1 mr-2 h-5 w-5" />
                    Analyzing...
                  </>
                ) : (
                  <>
                    <Search className="-ml-1 mr-2 h-5 w-5" />
                    Run Agents
                  </>
                )}
              </button>
            </div>
          </form>
        </div>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 dark:bg-red-900/30 p-4 border border-red-200 dark:border-red-800">
          <div className="flex">
            <div className="flex-shrink-0">
              <AlertCircle className="h-5 w-5 text-red-400" />
            </div>
            <div className="ml-3">
              <h3 className="text-sm font-medium text-red-800 dark:text-red-200">Analysis Failed</h3>
              <div className="mt-2 text-sm text-red-700 dark:text-red-300">{error}</div>
            </div>
          </div>
        </div>
      )}

      {result && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Agent 1: Research */}
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Search className="h-5 w-5 text-indigo-500" />
                <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Research Agent</h3>
              </div>
              <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-200">
                Data Gathered
              </span>
            </div>
            <div className="p-6">
              {result.research_data ? (
                <div className="space-y-4">
                  <div>
                    <h4 className="text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">News & Context</h4>
                    <div className="prose prose-sm dark:prose-invert max-w-none text-gray-700 dark:text-gray-300 bg-gray-50 dark:bg-gray-900 p-4 rounded-lg border border-gray-100 dark:border-gray-700 whitespace-pre-wrap">
                      {result.research_data.news_context || "No context retrieved."}
                    </div>
                  </div>
                  <div>
                    <h4 className="text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">Fundamentals Valid</h4>
                    <div className="flex items-center text-sm">
                      {result.research_data.fundamentals_valid ? (
                        <span className="flex items-center text-green-600 dark:text-green-400">
                          <CheckCircle2 className="h-4 w-4 mr-1.5" />
                          Passed
                        </span>
                      ) : (
                        <span className="flex items-center text-yellow-600 dark:text-yellow-400">
                          <AlertCircle className="h-4 w-4 mr-1.5" />
                          Warnings Present
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-gray-500 italic">No research data available.</p>
              )}
            </div>
          </div>

          {/* Agent 2: Execution */}
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <MessageSquare className="h-5 w-5 text-purple-500" />
                <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Execution Agent</h3>
              </div>
              <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${result.execution_plan?.mode === "advisory" ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-200" : "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-200"}`}>
                {result.execution_plan?.mode?.toUpperCase() || "UNKNOWN"} MODE
              </span>
            </div>
            <div className="p-6">
              {result.execution_plan ? (
                <div className="space-y-4">
                  <div>
                    <h4 className="text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">Advisory Summary</h4>
                    <div className="prose prose-sm dark:prose-invert max-w-none text-gray-700 dark:text-gray-300 bg-gray-50 dark:bg-gray-900 p-4 rounded-lg border border-gray-100 dark:border-gray-700 whitespace-pre-wrap">
                      {result.execution_plan.advisory_summary || "No summary provided."}
                    </div>
                  </div>
                  
                  {result.execution_plan.hypothetical_orders && result.execution_plan.hypothetical_orders.length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">Proposed Orders</h4>
                      <ul className="space-y-2">
                        {result.execution_plan.hypothetical_orders.map((order, idx) => (
                          <li key={idx} className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-900 rounded-lg border border-gray-100 dark:border-gray-700">
                            <div className="flex items-center">
                              <span className={`font-bold mr-2 ${order.action === "BUY" ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
                                {order.action}
                              </span>
                              <span className="font-semibold text-gray-900 dark:text-white">{order.symbol}</span>
                            </div>
                            <div className="text-sm text-gray-600 dark:text-gray-400">
                              {order.quantity ? `${order.quantity} shares` : order.weight ? `${(order.weight * 100).toFixed(1)}%` : ""}
                            </div>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  
                  {result.execution_plan.fallback_activated && (
                     <div className="mt-4 flex items-center text-sm text-yellow-600 dark:text-yellow-400 bg-yellow-50 dark:bg-yellow-900/20 p-3 rounded-md">
                       <AlertCircle className="h-4 w-4 mr-2 flex-shrink-0" />
                       Fallback heuristic activated due to LLM failure or timeout.
                     </div>
                  )}
                </div>
              ) : (
                <p className="text-sm text-gray-500 italic">No execution plan generated.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
