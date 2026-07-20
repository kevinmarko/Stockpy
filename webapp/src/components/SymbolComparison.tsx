import { useMemo, useState } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { api } from "../api/client";
import type { SymbolCompareResponse, UniverseResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading } from "./ui";
import { theme } from "../theme";
import { fmtNum, fmtPct } from "../format";

// Mirrors the legacy Streamlit Strategy Matrix's hard cap
// (`gui/panels/strategy_matrix.py::_render_symbol_comparison`'s
// `st.multiselect(..., max_selections=3)`) — the endpoint itself accepts up
// to 5 (matching this same screen's Pilot-vs-Pilot selector), but the UI
// keeps the legacy "2-3 recommended" feel rather than inviting a busy,
// hard-to-read 5-column table for a per-symbol comparison.
const MAX_SELECTED = 3;
const MIN_SELECTED = 2;
const STORAGE_KEY = "symbol_comparison_selected_symbols";
const CHART_COLORS = ["#38bdf8", "#10b981", "#f59e0b", "#a855f7", "#ec4899"];

/**
 * Symbol-vs-symbol comparison — the PWA port of the legacy Strategy Matrix's
 * "Symbol Comparison" section (`gui/panels/strategy_matrix.py:386-462`). Pick
 * 2-3 tracked symbols and see final blended score, action, Kelly Target,
 * conviction, GARCH vol, meta-label composite, and regime multiplier side by
 * side, plus the per-module weighted score-component breakdown as a grouped
 * bar chart — answering "why did A score higher than B" directly in the UI.
 *
 * Lives as its own card on the Compare screen, independent of the Pilot-vs-
 * Pilot comparison above it (different entities, same "pick N and compare"
 * pattern already established there).
 *
 * Honesty (CONSTRAINT #4): a symbol not in the latest snapshot still renders
 * a row (labeled "not tracked"), never silently dropped or hard-failed. Every
 * null numeric leaf renders "—", never a fabricated 0. `meta_label_composite`/
 * `regime_multiplier` are legitimately `null` whenever the strategy engine
 * didn't produce a value for that symbol this cycle — an honest absence, not
 * a bug.
 */
export function SymbolComparison() {
  const [selected, setSelected] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      const parsed = saved ? JSON.parse(saved) : [];
      return Array.isArray(parsed) ? parsed.filter((s) => typeof s === "string") : [];
    } catch {
      return [];
    }
  });

  const universe = useApi<UniverseResponse>(() => api.getUniverse(), []);
  const compare = useApi<SymbolCompareResponse | null>(
    () =>
      selected.length >= MIN_SELECTED
        ? api.getSymbolsCompare(selected)
        : Promise.resolve(null),
    [selected.join(",")]
  );

  const toggleSelect = (symbol: string) => {
    const next = selected.includes(symbol)
      ? selected.filter((s) => s !== symbol)
      : selected.length >= MAX_SELECTED
        ? selected
        : [...selected, symbol];
    setSelected(next);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* localStorage unavailable — selection still works for this session */
    }
  };

  const clearAll = () => {
    setSelected([]);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify([]));
    } catch {
      /* ignore */
    }
  };

  // Chart series: only FOUND symbols with a real score-component breakdown —
  // a "not tracked" symbol is never drawn as a phantom all-zero bar series.
  const chartSymbols = useMemo(
    () => (compare.data?.symbols ?? []).filter((s) => s.found && s.score_components),
    [compare.data]
  );
  const chartData = useMemo(() => {
    if (!compare.data || chartSymbols.length === 0) return [];
    return compare.data.modules.map((mod) => {
      const row: Record<string, string | number> = { module: mod };
      chartSymbols.forEach((s) => {
        const v = s.score_components?.[mod];
        if (v !== undefined) row[s.symbol] = v;
      });
      return row;
    });
  }, [compare.data, chartSymbols]);

  const notTracked = (compare.data?.symbols ?? []).filter((s) => !s.found);

  return (
    <section className="card card-pad" style={{ marginBottom: 16 }} data-testid="symbol-comparison">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <h2 style={{ fontSize: 16, margin: 0 }}>Symbol Comparison</h2>
        {selected.length > 0 && (
          <button className="btn btn-neutral" onClick={clearAll} style={{ fontSize: 12, padding: "4px 8px" }}>
            Clear All
          </button>
        )}
      </div>
      <p style={{ margin: "0 0 12px", fontSize: 13, color: theme.textMuted }}>
        Pick {MIN_SELECTED}-{MAX_SELECTED} tracked symbols to see score, sizing, and
        volatility side by side.
      </p>

      {universe.loading ? (
        <Loading lines={1} />
      ) : universe.error || !universe.data ? (
        <ErrorState message={universe.error ?? "No data"} status={universe.status} onRetry={universe.reload} />
      ) : universe.data.symbols.length === 0 ? (
        <div className="empty" data-testid="symbol-comparison-empty-universe">
          No tracked symbols yet — run the pipeline to populate the universe.
        </div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 16 }}>
          {universe.data.symbols.map((row) => {
            const checked = selected.includes(row.symbol);
            const disabled = !checked && selected.length >= MAX_SELECTED;
            return (
              <label
                key={row.symbol}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  background: checked ? theme.surface3 : theme.surface2,
                  padding: "6px 12px",
                  borderRadius: 20,
                  border: `1px solid ${checked ? theme.accent : theme.border}`,
                  cursor: disabled ? "not-allowed" : "pointer",
                  opacity: disabled ? 0.5 : 1,
                  fontSize: 13,
                }}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={disabled}
                  onChange={() => toggleSelect(row.symbol)}
                  style={{ cursor: "pointer" }}
                  data-testid={`symbol-comparison-checkbox-${row.symbol}`}
                />
                {row.symbol}
              </label>
            );
          })}
        </div>
      )}

      {selected.length < MIN_SELECTED ? (
        <div className="empty" data-testid="symbol-comparison-select-more" style={{ padding: 24 }}>
          Select at least {MIN_SELECTED} symbols above to compare.
        </div>
      ) : compare.loading ? (
        <Loading lines={3} />
      ) : compare.error || !compare.data ? (
        <ErrorState message={compare.error ?? "No data"} status={compare.status} onRetry={compare.reload} />
      ) : (
        <>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.borderStrong}` }}>
                  <th style={{ padding: 8 }}>Metric</th>
                  {compare.data.symbols.map((s) => (
                    <th key={s.symbol} style={{ padding: 8, color: theme.accent }}>
                      {s.symbol}
                      {!s.found && (
                        <span style={{ display: "block", fontSize: 10, color: theme.textMuted, fontWeight: 400 }}>
                          not tracked
                        </span>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>Final Score</td>
                  {compare.data.symbols.map((s) => (
                    <td key={s.symbol} style={{ padding: 8 }} className="num">{fmtNum(s.score, 1)}</td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>Action</td>
                  {compare.data.symbols.map((s) => (
                    <td key={s.symbol} style={{ padding: 8 }}>{s.action ?? "—"}</td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>Kelly Target</td>
                  {compare.data.symbols.map((s) => (
                    <td key={s.symbol} style={{ padding: 8 }} className="num">
                      {fmtPct(s.kelly_target, 1, { fromFraction: true })}
                    </td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>Conviction</td>
                  {compare.data.symbols.map((s) => (
                    <td key={s.symbol} style={{ padding: 8 }} className="num">
                      {fmtPct(s.conviction, 0, { fromFraction: true })}
                    </td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>GARCH Vol</td>
                  {compare.data.symbols.map((s) => (
                    <td key={s.symbol} style={{ padding: 8 }} className="num">{fmtNum(s.garch_vol, 3)}</td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>Meta-Label Composite</td>
                  {compare.data.symbols.map((s) => (
                    <td key={s.symbol} style={{ padding: 8 }} className="num">
                      {fmtNum(s.meta_label_composite, 2)}
                    </td>
                  ))}
                </tr>
                <tr style={{ borderBottom: `1px solid ${theme.border}` }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>Regime Multiplier</td>
                  {compare.data.symbols.map((s) => (
                    <td key={s.symbol} style={{ padding: 8 }} className="num">
                      {fmtNum(s.regime_multiplier, 2)}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>

          {notTracked.length > 0 && (
            <div
              data-testid="symbol-comparison-not-tracked-note"
              className="empty"
              style={{ marginTop: 12, padding: "12px", background: "var(--surface-2)", borderRadius: 12, fontSize: 13 }}
            >
              Not tracked in the latest snapshot: {notTracked.map((s) => s.symbol).join(", ")}.
            </div>
          )}

          <h3 style={{ fontSize: 14, margin: "16px 0 8px" }}>Score-component breakdown</h3>
          {chartData.length === 0 ? (
            <div className="empty" data-testid="symbol-comparison-no-components" style={{ padding: 24 }}>
              No score-component breakdown available for the selected symbols this cycle.
            </div>
          ) : (
            <div style={{ height: 260 }} data-testid="symbol-comparison-chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.05)" />
                  <XAxis
                    dataKey="module"
                    stroke={theme.textMuted}
                    fontSize={10}
                    tickLine={false}
                    angle={-30}
                    textAnchor="end"
                    height={60}
                  />
                  <YAxis stroke={theme.textMuted} fontSize={10} tickLine={false} />
                  <Tooltip
                    contentStyle={{ background: theme.surface2, border: `1px solid ${theme.border}`, borderRadius: 4 }}
                    labelStyle={{ color: theme.textSecondary, fontSize: 11 }}
                    itemStyle={{ fontSize: 11 }}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {chartSymbols.map((s, index) => (
                    <Bar key={s.symbol} dataKey={s.symbol} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </section>
  );
}
