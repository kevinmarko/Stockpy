import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import type { TradeHistoryPage } from "../api/types";
import { useApi } from "../hooks/useApi";
import { TabGuide } from "../components/TabGuide";
import { Button, EmptyState, ErrorState, Loading, Select, Table, Tile } from "../components/ui";
import { fmtNum, fmtPct, fmtSignedUsd, timeAgo } from "../format";
import { theme } from "../theme";

const ANY_SYMBOL = "";
const PAGE_SIZE = 25;

/**
 * Trade History — the operator's REAL Robinhood closed-trade ledger, in
 * full and paginated. Distinct from the Portfolio screen's "Realized
 * performance" panel (which is a cache-only, 8-row-truncated summary feed):
 * this reads the durable store (`GET /portfolio/trade-history`,
 * `data/broker_fills_store.py`, fed by the login worker's orders ingest
 * during a `--refresh-account` login) and supports real pagination and a
 * per-symbol filter over the operator's entire ingested history.
 *
 * `get_all_stock_orders` (the Robinhood API this is built on) is
 * equities-only -- options and crypto activity are not covered here.
 */
export function TradeHistory() {
  const [offset, setOffset] = useState(0);
  const [symbol, setSymbol] = useState(ANY_SYMBOL);

  const page = useApi<TradeHistoryPage>(
    () => api.getTradeHistory({ limit: PAGE_SIZE, offset, symbol: symbol || undefined }),
    [offset, symbol]
  );

  const onSymbolChange = (next: string) => {
    setSymbol(next);
    setOffset(0); // a new filter always starts back at page 1
  };

  return (
    <div className="screen">
      <h1 className="screen-title">Trade History</h1>
      <p className="screen-sub">
        Every closed round-trip reconstructed by FIFO lot-matching of your real Robinhood
        filled-order history — not a simulation, and distinct from any internal paper trade.
      </p>

      <TabGuide tabKey="trade-history" />

      {page.loading && !page.data ? (
        <Loading lines={4} />
      ) : page.error && !page.data ? (
        <ErrorState message={page.error} status={page.status} onRetry={page.reload} />
      ) : !page.data || !page.data.available ? (
        <EmptyState
          title="No trade history ingested yet"
          hint="Run `python3 main.py --refresh-account` (a device-approval login) to fetch and persist your real Robinhood filled-order history."
        />
      ) : (
        <>
          <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
            <div className="tiles">
              <Tile
                label="Realized P&L"
                value={fmtSignedUsd(page.data.summary.total_realized_pnl)}
                tone={page.data.summary.total_realized_pnl >= 0 ? "pos" : "neg"}
              />
              <Tile
                label="Win rate"
                value={fmtPct(page.data.summary.win_rate, 0, { fromFraction: true })}
              />
              <Tile label="Profit factor" value={fmtNum(page.data.summary.profit_factor, 2)} />
              <Tile label="Trades" value={page.data.summary.n_trades} />
            </div>
            <p style={{ color: theme.textMuted, fontSize: "var(--t-footnote)", marginTop: "var(--s-2)" }}>
              {page.data.last_ingested_at
                ? `Last ingested ${timeAgo(page.data.last_ingested_at)}.`
                : "Ingest time unknown."}
            </p>
          </section>

          <section className="card card-pad">
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "var(--s-3)",
                marginBottom: "var(--s-3)",
              }}
            >
              <Select
                label="Symbol"
                value={symbol}
                onChange={(e) => onSymbolChange(e.target.value)}
                options={[
                  { value: ANY_SYMBOL, label: "All symbols" },
                  ...page.data.symbols.map((s) => ({ value: s, label: s })),
                ]}
              />
              <Button onClick={page.reload} disabled={page.loading}>
                Refresh
              </Button>
            </div>

            {page.data.trades.length === 0 ? (
              <EmptyState title="No trades match this filter" />
            ) : (
              <div style={{ overflowX: "auto" }}>
                <Table>
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th className="num">Shares</th>
                      <th>Entry</th>
                      <th>Exit</th>
                      <th className="num">Held</th>
                      <th className="num">Return</th>
                      <th className="num">Realized P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.data.trades.map((t, i) => (
                      <tr key={`${t.symbol}-${t.exit_ts}-${i}`}>
                        <td>
                          <Link to={`/symbol/${t.symbol}`}>{t.symbol}</Link>
                        </td>
                        <td className="num">{t.quantity == null ? "—" : fmtNum(t.quantity, 0)}</td>
                        <td>{t.entry_ts ? new Date(t.entry_ts).toLocaleDateString() : "—"}</td>
                        <td>{t.exit_ts ? new Date(t.exit_ts).toLocaleDateString() : "—"}</td>
                        <td className="num">
                          {t.holding_days == null ? "—" : `${fmtNum(t.holding_days, 0)}d`}
                        </td>
                        <td
                          className="num"
                          style={{
                            color:
                              t.return_pct == null
                                ? undefined
                                : t.return_pct >= 0
                                  ? theme.growth
                                  : theme.decline,
                          }}
                        >
                          {t.return_pct == null ? "—" : fmtPct(t.return_pct, 1, { signed: true })}
                        </td>
                        <td
                          className="num"
                          style={{
                            fontWeight: 700,
                            color:
                              t.realized_pnl == null
                                ? undefined
                                : t.realized_pnl >= 0
                                  ? theme.growth
                                  : theme.decline,
                          }}
                        >
                          {t.realized_pnl == null ? "—" : fmtSignedUsd(t.realized_pnl)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            )}

            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginTop: "var(--s-3)",
              }}
            >
              <span style={{ color: theme.textMuted, fontSize: "var(--t-footnote)" }}>
                {page.data.total === 0
                  ? "0 trades"
                  : `${offset + 1}–${Math.min(offset + PAGE_SIZE, page.data.total)} of ${page.data.total}`}
              </span>
              <div style={{ display: "flex", gap: "var(--s-2)" }}>
                <Button
                  onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                  disabled={offset === 0 || page.loading}
                >
                  Previous
                </Button>
                <Button
                  onClick={() => setOffset((o) => o + PAGE_SIZE)}
                  disabled={offset + PAGE_SIZE >= page.data.total || page.loading}
                >
                  Next
                </Button>
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
