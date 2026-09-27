import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { TabGuide } from "../components/TabGuide";
import { Modal } from "../components/Modal";
import { RetrospectiveDetailModal } from "../components/RetrospectiveDetailModal";
import { SymbolInput } from "../components/SymbolInput";
import { theme } from "../theme";
import { EquityOrderTicket } from "../components/EquityOrderTicket";
import type { Quote } from "../api/types";

// Loading/error placeholder for the screen's core (always-on) data sections.
function CoreSectionLoading({ label }: { label: string }) {
  return (
    <div style={{ padding: 24, textAlign: "center", color: theme.textSecondary, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
      {label}
    </div>
  );
}

function CoreSectionError({ label, error }: { label: string; error: string }) {
  return (
    <div style={{ padding: 24, textAlign: "center", color: theme.decline, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
      {label}: {error}
    </div>
  );
}

export function PaperBroker() {
  const account = useApi(() => api.getPaperBrokerAccount());
  const positions = useApi(() => api.getPaperBrokerPositions());
  const orders = useApi(() => api.getPaperBrokerOrders(100));
  const closedTrades = useApi(() => api.getPaperBrokerClosedTrades(100));

  const [showResetModal, setShowResetModal] = useState(false);
  const [resetCash, setResetCash] = useState(100000);
  const [retroTradeId, setRetroTradeId] = useState<number | null>(null);

  // Quick Trade state -- a free-text "trade any FMP-quotable symbol" entry
  // point. Everything else on this screen (positions, orders) only ever
  // surfaces symbols the platform already tracks; this is the one path that
  // lets the operator paper-trade a ticker outside the pipeline's
  // watchlist/universe, which the backend has always allowed
  // (execution/fmp_paper_broker.py has no watchlist/universe gate).
  const [searchParams] = useSearchParams();
  const [quickTradeSymbol, setQuickTradeSymbol] = useState<string | null>(null);
  const [quickTradeQuote, setQuickTradeQuote] = useState<Quote | null>(null);
  const [quickTradeError, setQuickTradeError] = useState<string | null>(null);
  const [quickTradeLoading, setQuickTradeLoading] = useState(false);

  const resetMutation = useMutation((cash: number) => api.resetPaperBroker(cash));

  const handleReset = async () => {
    await resetMutation.run(resetCash);
    if (!resetMutation.error) {
      setShowResetModal(false);
      account.reload();
      positions.reload();
      orders.reload();
    }
  };

  const handleQuickTradeSubmit = async (symbol: string) => {
    setQuickTradeError(null);
    setQuickTradeQuote(null);
    setQuickTradeSymbol(null);
    setQuickTradeLoading(true);
    try {
      const quotes = await api.getDataQuotes([symbol]);
      const quote = quotes[symbol];
      // Fail closed (CONSTRAINT #6): no quote, or a quote with a null price,
      // must never open an order ticket seeded with a fabricated $0 spot
      // price -- surface an honest error instead.
      if (!quote || quote.price == null) {
        setQuickTradeError(`No live quote available for "${symbol}". Check the ticker and try again.`);
      } else {
        setQuickTradeQuote(quote);
        setQuickTradeSymbol(symbol);
      }
    } catch (e) {
      setQuickTradeError(e instanceof Error ? e.message : "Quote lookup failed.");
    } finally {
      setQuickTradeLoading(false);
    }
  };

  // One-way, fire-and-forget handoff from webapp/src/screens/SymbolScreener.tsx
  // -- the only cross-screen data-passing pattern already used in this
  // codebase (matches Commands.tsx's `?builder=` param), so no new
  // store/context is introduced. Fires at most once per mount (a `useRef`
  // guard, not the `searchParams` object itself, since a fresh
  // `URLSearchParams` instance is constructed on every render and would
  // otherwise refire the quote lookup on every re-render).
  const quickTradeHandoffDone = useRef(false);
  useEffect(() => {
    if (quickTradeHandoffDone.current) return;
    const symbol = searchParams.get("quickTradeSymbol");
    if (symbol) {
      quickTradeHandoffDone.current = true;
      void handleQuickTradeSubmit(symbol.trim().toUpperCase());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", width: "100%", overflow: "hidden" }}>
      <div style={{
        padding: "16px 24px",
        borderBottom: `1px solid ${theme.border}`,
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        flexShrink: 0
      }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 600 }}>Paper Broker</h1>
          <div style={{ fontSize: 12, color: theme.textSecondary, marginTop: 2 }}>
            Simulated execution against real market quotes
          </div>
        </div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <button
            onClick={() => setShowResetModal(true)}
            style={{
              padding: "8px 14px",
              background: theme.surface,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              cursor: "pointer",
              fontWeight: 500,
              fontSize: 13,
            }}
          >
            Reset Paper Account
          </button>
        </div>
      </div>


      <div style={{ flex: 1, overflowY: "auto", padding: 24, display: "flex", flexDirection: "column", gap: 24 }}>
        <TabGuide tabKey="paper-broker" />

        {/* Quick Trade -- paper-trade ANY FMP-quotable symbol, not just what's
            already tracked in your watchlist/pipeline universe. Everything
            else on this screen (positions, orders)
            only ever surfaces symbols the platform already knows about. */}
        <div style={{
          padding: 20,
          background: theme.surface,
          borderRadius: 8,
          border: `1px solid ${theme.border}`,
          display: "flex",
          flexDirection: "column",
          gap: 12
        }}>
          <div>
            <h2 style={{ fontSize: 18, fontWeight: 600, margin: "0 0 4px 0" }}>🔍 Quick Trade — Any Symbol</h2>
            <div style={{ color: theme.textSecondary, fontSize: 13 }}>
              Paper-trade any FMP-quotable ticker, even one outside your tracked watchlist.
            </div>
            <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 4 }}>
              This is a manual, paper-only trade — Autopilot's automated signals only act on
              symbols in your tracked universe (watchlist, holdings, or discovered scan
              candidates), so a Quick Trade here doesn't change what it does on its own.
            </div>
          </div>
          <SymbolInput
            key={searchParams.get("quickTradeSymbol") ?? "quick-trade"}
            initial={searchParams.get("quickTradeSymbol")?.trim().toUpperCase() ?? ""}
            label="Symbol"
            hint="Enter any ticker FMP can quote — not limited to your tracked watchlist."
            onSubmit={handleQuickTradeSubmit}
            pending={quickTradeLoading}
            buttonText="Get Quote"
            testId="quick-trade-symbol-input"
            requireExactMatch
            autoSubmitOnExactMatch
          />
          {quickTradeError && (
            <div style={{
              padding: "10px 14px",
              background: "rgba(239, 68, 68, 0.15)",
              color: theme.decline,
              borderRadius: 6,
              fontSize: 13,
              fontWeight: 500
            }}>
              {quickTradeError}
            </div>
          )}
          {quickTradeSymbol && quickTradeQuote && quickTradeQuote.price != null && (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontSize: 13, color: theme.textSecondary }}>
                  {quickTradeSymbol}: <strong style={{ color: theme.textPrimary }}>${quickTradeQuote.price.toFixed(2)}</strong>
                  {quickTradeQuote.is_stale && <span style={{ color: theme.caution, marginLeft: 6 }}>(delayed)</span>}
                </div>
              </div>
              <EquityOrderTicket
                key={`quick-trade-${quickTradeSymbol}`}
                symbol={quickTradeSymbol}
                spotPrice={quickTradeQuote.price}
                onClear={() => {
                  setQuickTradeSymbol(null);
                  setQuickTradeQuote(null);
                  account.reload();
                  positions.reload();
                  orders.reload();
                }}
              />
            </>
          )}
        </div>

        {account.loading && !account.data && (
          <CoreSectionLoading label="Loading account summary..." />
        )}
        {account.error && !account.data && (
          <CoreSectionError label="Failed to load account summary" error={account.error} />
        )}
        {account.data && (
          <div style={{ display: "flex", gap: 16 }}>
            <div style={{ flex: 1, padding: 16, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
              <div style={{ color: theme.textSecondary, fontSize: 13, marginBottom: 4 }}>Equity</div>
              <div style={{ fontSize: 24, fontWeight: 600 }}>${account.data.equity.toLocaleString("en-US", { minimumFractionDigits: 2 })}</div>
            </div>
            <div style={{ flex: 1, padding: 16, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
              <div style={{ color: theme.textSecondary, fontSize: 13, marginBottom: 4 }}>Cash</div>
              <div style={{ fontSize: 24, fontWeight: 600 }}>${account.data.cash.toLocaleString("en-US", { minimumFractionDigits: 2 })}</div>
            </div>
            <div style={{ flex: 1, padding: 16, background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}` }}>
              <div style={{ color: theme.textSecondary, fontSize: 13, marginBottom: 4 }}>Buying Power</div>
              <div style={{ fontSize: 24, fontWeight: 600 }}>${account.data.buying_power.toLocaleString("en-US", { minimumFractionDigits: 2 })}</div>
            </div>
          </div>
        )}

        <div>
          <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 12 }}>Positions</h2>
          <div style={{ background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}`, overflow: "hidden" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Symbol</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Strategy</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Qty</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Avg Cost</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Current Price</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Market Value</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Unrealized P&L</th>
                </tr>
              </thead>
              <tbody>
                {positions.loading && !positions.data && (
                  <tr>
                    <td colSpan={7} style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>Loading positions...</td>
                  </tr>
                )}
                {positions.error && !positions.data && (
                  <tr>
                    <td colSpan={7} style={{ padding: 24, textAlign: "center", color: theme.decline }}>Failed to load positions: {positions.error}</td>
                  </tr>
                )}
                {positions.data?.length === 0 && (
                  <tr>
                    <td colSpan={7} style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>No open positions</td>
                  </tr>
                )}
                {positions.data?.map(p => {
                  const isOption = p.symbol.includes(" ") && p.symbol.includes("$");
                  const isShort = p.qty < 0;
                  return (
                    <tr key={p.symbol} style={{ borderBottom: `1px solid ${theme.border}` }}>
                      <td style={{ padding: "12px 16px", fontWeight: 500 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span>{p.symbol}</span>
                          {isOption && (
                            <span style={{
                              fontSize: 10,
                              fontWeight: 600,
                              padding: "2px 6px",
                              borderRadius: 4,
                              background: "rgba(99, 102, 241, 0.15)",
                              color: "#818cf8",
                              letterSpacing: 0.5
                            }}>
                              OPTION
                            </span>
                          )}
                          {isShort && (
                            <span style={{
                              fontSize: 10,
                              fontWeight: 600,
                              padding: "2px 6px",
                              borderRadius: 4,
                              background: "rgba(239, 68, 68, 0.15)",
                              color: theme.decline,
                              letterSpacing: 0.5
                            }}>
                              SHORT
                            </span>
                          )}
                        </div>
                      </td>
                      <td style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 13 }}>
                        {p.strategy_id ?? "—"}
                      </td>
                      <td style={{ padding: "12px 16px", textAlign: "right", fontWeight: isShort ? 600 : 400, color: isShort ? theme.decline : theme.textPrimary }}>
                        {p.qty}
                      </td>
                      <td style={{ padding: "12px 16px", textAlign: "right" }}>${p.avg_cost.toFixed(2)}</td>
                      <td style={{ padding: "12px 16px", textAlign: "right" }}>{p.current_price != null ? `$${p.current_price.toFixed(2)}` : "—"}</td>
                      <td style={{ padding: "12px 16px", textAlign: "right" }}>{p.market_value != null ? `$${p.market_value.toFixed(2)}` : "—"}</td>
                      <td style={{ padding: "12px 16px", textAlign: "right", color: (p.unrealized_pl ?? 0) >= 0 ? theme.growth : theme.decline }}>
                        {p.unrealized_pl != null ? `$${p.unrealized_pl.toFixed(2)}` : "—"}
                        {p.unrealized_pl_pct != null && ` (${(p.unrealized_pl_pct * 100).toFixed(2)}%)`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div>

          <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 12 }}>Orders (Last 100)</h2>
          <div style={{ background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}`, overflow: "hidden" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Date</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Symbol</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Strategy</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Side</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Qty</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Price</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Status</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Filled</th>
                </tr>
              </thead>
              <tbody>
                {orders.loading && !orders.data && (
                  <tr>
                    <td colSpan={8} style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>Loading orders...</td>
                  </tr>
                )}
                {orders.error && !orders.data && (
                  <tr>
                    <td colSpan={8} style={{ padding: 24, textAlign: "center", color: theme.decline }}>Failed to load orders: {orders.error}</td>
                  </tr>
                )}
                {orders.data?.length === 0 && (
                  <tr>
                    <td colSpan={8} style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>No recent orders</td>
                  </tr>
                )}
                {orders.data?.map(o => (
                  <tr key={o.order_id} style={{ borderBottom: `1px solid ${theme.border}` }}>
                    <td style={{ padding: "12px 16px", whiteSpace: "nowrap" }}>{new Date(o.created_at).toLocaleString()}</td>
                    <td style={{ padding: "12px 16px", fontWeight: 500 }}>{o.symbol}</td>
                    <td style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 13 }}>{o.strategy_id ?? "—"}</td>
                    <td style={{ padding: "12px 16px", color: o.side === "BUY" ? theme.growth : theme.decline }}>{o.side}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>{o.qty}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>${o.price.toFixed(2)}</td>
                    <td style={{ padding: "12px 16px" }}>{o.status}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>
                      {o.filled_qty} {o.filled_avg_price != null ? ` @ $${o.filled_avg_price.toFixed(2)}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>Closed Trades</h2>
            <Link
              to="/retrospective"
              style={{
                fontSize: 13,
                color: "#818cf8",
                textDecoration: "none",
                fontWeight: 600,
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              View Retrospective Journal &rarr;
            </Link>
          </div>
          <div style={{ background: theme.surface, borderRadius: 8, border: `1px solid ${theme.border}`, overflow: "hidden" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Exit Time</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Symbol</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Strategy</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Side</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Qty</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Entry Price</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Exit Price</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Realized P&L</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "right" }}>Realized P&L %</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600 }}>Close Reason</th>
                  <th style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 12, fontWeight: 600, textAlign: "center" }}>Autopsy</th>
                </tr>
              </thead>
              <tbody>
                {closedTrades.loading && !closedTrades.data && (
                  <tr>
                    <td colSpan={11} style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>Loading closed trades...</td>
                  </tr>
                )}
                {closedTrades.error && !closedTrades.data && (
                  <tr>
                    <td colSpan={11} style={{ padding: 24, textAlign: "center", color: theme.decline }}>Failed to load closed trades: {closedTrades.error}</td>
                  </tr>
                )}
                {closedTrades.data?.length === 0 && (
                  <tr>
                    <td colSpan={11} style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>No closed trades</td>
                  </tr>
                )}
                {closedTrades.data?.map(t => (
                  <tr key={t.trade_id} style={{ borderBottom: `1px solid ${theme.border}` }}>
                    <td style={{ padding: "12px 16px", whiteSpace: "nowrap" }}>{new Date(t.exit_ts).toLocaleString()}</td>
                    <td style={{ padding: "12px 16px", fontWeight: 500 }}>{t.symbol}</td>
                    <td style={{ padding: "12px 16px", color: theme.textSecondary, fontSize: 13 }}>{t.strategy_id ?? "—"}</td>
                    <td style={{ padding: "12px 16px", color: t.side === "BUY" ? theme.growth : theme.decline }}>{t.side}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>{t.qty}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>${t.entry_price.toFixed(2)}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right" }}>${t.exit_price.toFixed(2)}</td>
                    <td style={{ padding: "12px 16px", textAlign: "right", color: t.realized_pnl >= 0 ? theme.growth : theme.decline }}>
                      ${t.realized_pnl.toFixed(2)}
                    </td>
                    <td style={{ padding: "12px 16px", textAlign: "right", color: (t.realized_pnl_pct ?? 0) >= 0 ? theme.growth : theme.decline }}>
                      {t.realized_pnl_pct != null ? `${(t.realized_pnl_pct * 100).toFixed(2)}%` : "—"}
                    </td>
                    <td style={{ padding: "12px 16px", color: theme.textSecondary }}>{t.close_reason}</td>
                    <td style={{ padding: "12px 16px", textAlign: "center" }}>
                      <button
                        type="button"
                        className="btn btn-neutral"
                        onClick={() => setRetroTradeId(t.trade_id)}
                        style={{ padding: "4px 8px", fontSize: 11 }}
                      >
                        Autopsy &rarr;
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>


      {showResetModal && (
        <Modal ariaLabel="Reset Paper Broker" onClose={() => setShowResetModal(false)}>
          <div style={{ padding: 24 }}>
            <h2 style={{ margin: "0 0 16px 0" }}>Reset Paper Broker</h2>
            <p style={{ margin: "0 0 16px 0", color: theme.textSecondary, lineHeight: 1.5 }}>
              This will wipe all paper positions and orders and reset your account to the specified starting cash.
            </p>
            <div style={{ marginBottom: 24 }}>
              <label style={{ display: "block", marginBottom: 8, fontSize: 14, fontWeight: 500 }}>Starting Cash</label>
              <input 
                type="number" 
                value={resetCash} 
                onChange={e => setResetCash(Number(e.target.value))}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: theme.base,
                  border: `1px solid ${theme.border}`,
                  color: theme.textPrimary,
                  borderRadius: 4
                }}
              />
            </div>
            {resetMutation.error && (
              <div style={{ padding: 12, background: "rgba(239, 68, 68, 0.1)", color: theme.decline, borderRadius: 4, marginBottom: 16 }}>
                {resetMutation.error}
              </div>
            )}
            <div style={{ display: "flex", gap: 12, justifyContent: "flex-end" }}>
              <button 
                onClick={() => setShowResetModal(false)}
                style={{
                  padding: "8px 16px",
                  background: "transparent",
                  border: "none",
                  color: theme.textSecondary,
                  cursor: "pointer",
                  fontWeight: 500
                }}
              >
                Cancel
              </button>
              <button 
                onClick={handleReset}
                disabled={resetMutation.pending}
                style={{
                  padding: "8px 16px",
                  background: theme.decline,
                  border: "none",
                  color: "#fff",
                  borderRadius: 4,
                  cursor: resetMutation.pending ? "not-allowed" : "pointer",
                  fontWeight: 500
                }}
              >
                {resetMutation.pending ? "Resetting..." : "Reset"}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Retrospective Autopsy Modal */}
      <RetrospectiveDetailModal
        isOpen={retroTradeId !== null}
        tradeId={retroTradeId}
        onClose={() => setRetroTradeId(null)}
      />
    </div>
  );
}
