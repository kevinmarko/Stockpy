import React, { useEffect, useState } from "react";
import { X, ExternalLink, RefreshCw, BarChart2, Shield, Compass, TrendingUp } from "lucide-react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { api } from "../api/client";
import { useExplainTicker } from "../context/ExplainTickerContext";
import type { ExplainTickerResponse, Bar } from "../api/types";
import { Loading } from "./ui";
import { fmtUsd, fmtNum, timeAgo } from "../format";

export interface ExplainTickerDrawerProps {
  symbol?: string;
  isOpen?: boolean;
  onClose?: () => void;
}

export const ExplainTickerDrawer: React.FC<ExplainTickerDrawerProps> = ({
  symbol: propSymbol,
  isOpen: propIsOpen,
  onClose: propOnClose,
}) => {
  const context = useExplainTicker();
  const isOpen = propIsOpen !== undefined ? propIsOpen : context.isOpen;
  const activeSymbol = propSymbol || context.symbol;
  const handleClose = propOnClose || context.closeExplainTicker;

  const [data, setData] = useState<ExplainTickerResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [barsLoading, setBarsLoading] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [backfillResult, setBackfillResult] = useState<string | null>(null);

  // Close on Escape key press
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        handleClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, handleClose]);

  // Fetch explain data whenever activeSymbol or isOpen changes
  useEffect(() => {
    if (!isOpen || !activeSymbol) {
      setData(null);
      setBars(null);
      setError(null);
      setBackfillResult(null);
      return;
    }

    let active = true;
    setLoading(true);
    setError(null);

    api
      .getExplainTicker(activeSymbol)
      .then((res) => {
        if (!active) return;
        setData(res);
        setLoading(false);

        // Fetch bars if available according to price_history_status
        if (res.price_history_status.available && res.price_history_status.bar_count > 0) {
          setBarsLoading(true);
          api
            .getDataBars(activeSymbol, 90)
            .then((b) => {
              if (active) setBars(b);
            })
            .catch(() => {
              if (active) setBars([]);
            })
            .finally(() => {
              if (active) setBarsLoading(false);
            });
        } else {
          setBars([]);
        }
      })
      .catch((err: any) => {
        if (!active) return;
        setError(err?.message || `Failed to load explain data for ${activeSymbol}`);
        setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [isOpen, activeSymbol]);

  const handleBackfill = async () => {
    if (!activeSymbol || backfilling) return;
    setBackfilling(true);
    setBackfillResult(null);
    try {
      const res = await api.triggerSymbolBackfill(activeSymbol);
      setBackfillResult(`Backfill triggered (${res.status})`);
      // Reload explain data
      const updated = await api.getExplainTicker(activeSymbol);
      setData(updated);
      if (updated.price_history_status.available && updated.price_history_status.bar_count > 0) {
        const b = await api.getDataBars(activeSymbol, 90);
        setBars(b);
      }
    } catch (e: any) {
      setBackfillResult(`Backfill failed: ${e?.message || "Unknown error"}`);
    } finally {
      setBackfilling(false);
    }
  };

  if (!isOpen || !activeSymbol) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="sheet-backdrop"
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0, 0, 0, 0.6)",
          backdropFilter: "blur(2px)",
          zIndex: 8999,
        }}
        onClick={handleClose}
        aria-hidden="true"
      />

      {/* Drawer */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Explain ${activeSymbol}`}
        data-testid="explain-ticker-drawer"
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          width: "min(100vw, 520px)",
          background: "var(--surface)",
          borderLeft: "1px solid var(--border)",
          boxShadow: "-10px 0 30px rgba(0, 0, 0, 0.5)",
          zIndex: 9000,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: "var(--s-4)",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            background: "var(--surface-2)",
          }}
        >
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
              <h2
                style={{
                  margin: 0,
                  fontSize: "var(--t-title)",
                  fontWeight: 700,
                  color: "var(--text-primary)",
                }}
              >
                {activeSymbol}
              </h2>
              {data?.company_profile.exchange && (
                <span
                  style={{
                    fontSize: "var(--t-caption)",
                    padding: "2px 6px",
                    borderRadius: "var(--r-xs)",
                    background: "var(--surface-3)",
                    color: "var(--text-muted)",
                  }}
                >
                  {data.company_profile.exchange}
                </span>
              )}
              {data?.company_profile.sector && (
                <span
                  style={{
                    fontSize: "var(--t-caption)",
                    padding: "2px 6px",
                    borderRadius: "var(--r-xs)",
                    background: "var(--surface-3)",
                    color: "var(--accent)",
                  }}
                >
                  {data.company_profile.sector}
                </span>
              )}
            </div>
            <div
              style={{
                color: "var(--text-muted)",
                fontSize: "var(--t-caption)",
                marginTop: "2px",
              }}
            >
              {data?.company_profile.company_name ?? "Explain This Ticker"}
            </div>
          </div>
          <button
            type="button"
            onClick={handleClose}
            aria-label="Close"
            data-testid="explain-drawer-close"
            style={{
              background: "none",
              border: "none",
              color: "var(--text-muted)",
              fontSize: "20px",
              cursor: "pointer",
              padding: "var(--s-1)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: "4px",
            }}
          >
            <X size={20} />
          </button>
        </div>

        {/* Scrollable Body */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "var(--s-4)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--s-4)",
          }}
        >
          {loading && <Loading lines={6} />}

          {!loading && error && (
            <div className="notice notice-warn" role="alert">
              <span>{error}</span>
            </div>
          )}

          {!loading && data && (
            <>
              {/* Section 1: Company Profile & Description */}
              <section
                data-testid="company-profile-section"
                style={{
                  background: "var(--surface-2)",
                  padding: "var(--s-3)",
                  borderRadius: "var(--r-sm)",
                  border: "1px solid var(--border)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: "var(--s-2)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--s-2)",
                      fontSize: "var(--t-subhead)",
                      fontWeight: 600,
                      color: "var(--text-primary)",
                    }}
                  >
                    <Compass size={16} />
                    <span>Company Profile</span>
                  </div>
                  {data.company_profile.source && (
                    <span
                      style={{
                        fontSize: "var(--t-caption)",
                        color: "var(--text-muted)",
                        textTransform: "uppercase",
                      }}
                    >
                      source: {data.company_profile.source}
                    </span>
                  )}
                </div>

                {data.company_profile.available ? (
                  <div>
                    {data.company_profile.description ? (
                      <p
                        style={{
                          fontSize: "var(--t-body)",
                          color: "var(--text-secondary)",
                          lineHeight: 1.5,
                          margin: "0 0 var(--s-3) 0",
                        }}
                      >
                        {data.company_profile.description}
                      </p>
                    ) : (
                      <p
                        style={{
                          fontSize: "var(--t-caption)",
                          color: "var(--text-muted)",
                          fontStyle: "italic",
                          margin: "0 0 var(--s-3) 0",
                        }}
                      >
                        No detailed description provided by profile source.
                      </p>
                    )}

                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(2, 1fr)",
                        gap: "var(--s-2)",
                        fontSize: "var(--t-caption)",
                      }}
                    >
                      {data.company_profile.industry && (
                        <div>
                          <span style={{ color: "var(--text-muted)" }}>Industry: </span>
                          <span style={{ color: "var(--text-primary)" }}>
                            {data.company_profile.industry}
                          </span>
                        </div>
                      )}
                      {data.company_profile.ceo && (
                        <div>
                          <span style={{ color: "var(--text-muted)" }}>CEO: </span>
                          <span style={{ color: "var(--text-primary)" }}>
                            {data.company_profile.ceo}
                          </span>
                        </div>
                      )}
                      {data.company_profile.market_cap != null && (
                        <div>
                          <span style={{ color: "var(--text-muted)" }}>Market Cap: </span>
                          <span style={{ color: "var(--text-primary)" }}>
                            {fmtUsd(data.company_profile.market_cap, { compact: true })}
                          </span>
                        </div>
                      )}
                      {data.company_profile.website && (
                        <div>
                          <span style={{ color: "var(--text-muted)" }}>Website: </span>
                          <a
                            href={data.company_profile.website}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{
                              color: "var(--accent)",
                              textDecoration: "none",
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "2px",
                            }}
                          >
                            <span>Link</span>
                            <ExternalLink size={11} />
                          </a>
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div
                    data-testid="profile-unavailable-notice"
                    style={{
                      fontSize: "var(--t-caption)",
                      color: "var(--text-muted)",
                      padding: "var(--s-2)",
                      background: "var(--surface)",
                      borderRadius: "var(--r-xs)",
                      border: "1px dashed var(--border)",
                    }}
                  >
                    Company description unavailable (
                    {data.company_profile.reason ||
                      `provider disabled or profile not found for ${activeSymbol}`}
                    )
                  </div>
                )}
              </section>

              {/* Section 2: Why It's Tracked */}
              <section
                data-testid="why-tracked-section"
                style={{
                  background: "var(--surface-2)",
                  padding: "var(--s-3)",
                  borderRadius: "var(--r-sm)",
                  border: "1px solid var(--border)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: "var(--s-2)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--s-2)",
                      fontSize: "var(--t-subhead)",
                      fontWeight: 600,
                      color: "var(--text-primary)",
                    }}
                  >
                    <Shield size={16} />
                    <span>Why It's Tracked</span>
                  </div>
                  <span
                    className={`badge badge-${data.tracking.coverage_status === "full" ? "good" : data.tracking.coverage_status === "uncovered" ? "bad" : "warn"}`}
                    style={{ fontSize: "var(--t-caption)", textTransform: "uppercase" }}
                  >
                    {data.tracking.coverage_status}
                  </span>
                </div>

                {data.tracking.tracked ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
                    {/* Holdings status */}
                    <div
                      style={{
                        padding: "var(--s-2)",
                        background: "var(--surface)",
                        borderRadius: "var(--r-xs)",
                        fontSize: "var(--t-caption)",
                      }}
                    >
                      <div style={{ fontWeight: 600, color: "var(--text-primary)", marginBottom: "4px" }}>
                        {data.tracking.held ? "Held in Portfolio" : "Not Held in Portfolio"}
                      </div>
                      {data.tracking.held && (
                        <div style={{ display: "flex", gap: "var(--s-4)", color: "var(--text-secondary)" }}>
                          {data.tracking.quantity != null && (
                            <div>Qty: <strong>{data.tracking.quantity}</strong></div>
                          )}
                          {data.tracking.avg_cost != null && (
                            <div>Avg: <strong>{fmtUsd(data.tracking.avg_cost)}</strong></div>
                          )}
                          {data.tracking.market_value != null && (
                            <div>Value: <strong>{fmtUsd(data.tracking.market_value)}</strong></div>
                          )}
                        </div>
                      )}
                    </div>

                    {/* Reasons */}
                    {data.tracking.reasons.length > 0 && (
                      <div style={{ fontSize: "var(--t-caption)" }}>
                        <span style={{ color: "var(--text-muted)" }}>Tracking reasons:</span>
                        <ul style={{ margin: "4px 0 0 16px", padding: 0, color: "var(--text-secondary)" }}>
                          {data.tracking.reasons.map((r, i) => (
                            <li key={i}>{r}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {/* Watchlists */}
                    {data.tracking.watchlists.length > 0 && (
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1)", fontSize: "var(--t-caption)" }}>
                        <span style={{ color: "var(--text-muted)" }}>Watchlists:</span>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                          {data.tracking.watchlists.map((w) => (
                            <span
                              key={w}
                              style={{
                                padding: "1px 6px",
                                background: "var(--surface-3)",
                                borderRadius: "4px",
                                color: "var(--text-secondary)",
                              }}
                            >
                              {w}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Rating streak & exclusion notice */}
                    {data.tracking.rating_consecutive_bad_cycles != null &&
                      data.tracking.rating_consecutive_bad_cycles > 0 && (
                        <div
                          style={{
                            padding: "var(--s-2)",
                            background: "rgba(234, 179, 8, 0.1)",
                            border: "1px solid rgba(234, 179, 8, 0.3)",
                            borderRadius: "var(--r-xs)",
                            fontSize: "var(--t-caption)",
                            color: "var(--caution)",
                          }}
                        >
                          Rating streak: {data.tracking.rating_consecutive_bad_cycles} consecutive BAD cycle(s)
                          {data.tracking.rating_excluded && " — Auto-excluded from advisory recommendations"}
                        </div>
                      )}
                  </div>
                ) : (
                  <div data-testid="untracked-notice">
                    <div
                      style={{
                        fontSize: "var(--t-caption)",
                        color: "var(--text-muted)",
                        padding: "var(--s-2)",
                        background: "var(--surface)",
                        borderRadius: "var(--r-xs)",
                        border: "1px dashed var(--border)",
                        marginBottom: "var(--s-2)",
                      }}
                    >
                      Not currently tracked in portfolio or watchlists.
                    </div>
                    <button
                      type="button"
                      className="btn btn-sm btn-secondary"
                      onClick={handleBackfill}
                      disabled={backfilling}
                      style={{ display: "inline-flex", alignItems: "center", gap: "var(--s-1)" }}
                    >
                      <RefreshCw size={12} className={backfilling ? "spin" : ""} />
                      <span>{backfilling ? "Adding..." : "Add to Universe / Backfill"}</span>
                    </button>
                  </div>
                )}
              </section>

              {/* Section 3: Factor Breakdown */}
              <section
                data-testid="factor-breakdown-section"
                style={{
                  background: "var(--surface-2)",
                  padding: "var(--s-3)",
                  borderRadius: "var(--r-sm)",
                  border: "1px solid var(--border)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: "var(--s-2)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--s-2)",
                      fontSize: "var(--t-subhead)",
                      fontWeight: 600,
                      color: "var(--text-primary)",
                    }}
                  >
                    <BarChart2 size={16} />
                    <span>Factor Breakdown</span>
                  </div>
                  {data.factor_breakdown.as_of && (
                    <span style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
                      {timeAgo(data.factor_breakdown.as_of)}
                    </span>
                  )}
                </div>

                {data.factor_breakdown.available ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
                    {/* Multifactor Tiles */}
                    {data.factor_breakdown.multifactor && (
                      <div>
                        <div
                          style={{
                            fontSize: "var(--t-caption)",
                            color: "var(--text-muted)",
                            marginBottom: "4px",
                            fontWeight: 600,
                          }}
                        >
                          Multifactor Components
                        </div>
                        <div
                          style={{
                            display: "grid",
                            gridTemplateColumns: "repeat(4, 1fr)",
                            gap: "var(--s-2)",
                          }}
                        >
                          {Object.entries(data.factor_breakdown.multifactor).map(([k, v]) => (
                            <div
                              key={k}
                              style={{
                                background: "var(--surface)",
                                padding: "var(--s-2)",
                                borderRadius: "var(--r-xs)",
                                textAlign: "center",
                              }}
                            >
                              <div style={{ fontSize: "10px", color: "var(--text-muted)", textTransform: "capitalize" }}>
                                {k.replace(/_/g, " ")}
                              </div>
                              <div style={{ fontSize: "var(--t-body)", fontWeight: 700, color: "var(--text-primary)" }}>
                                {v != null ? fmtNum(v, 2) : "—"}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Momentum & Technicals */}
                    {data.factor_breakdown.momentum && (
                      <div>
                        <div
                          style={{
                            fontSize: "var(--t-caption)",
                            color: "var(--text-muted)",
                            marginBottom: "4px",
                            fontWeight: 600,
                          }}
                        >
                          Momentum & Technicals
                        </div>
                        <div
                          style={{
                            display: "grid",
                            gridTemplateColumns: "repeat(3, 1fr)",
                            gap: "var(--s-2)",
                          }}
                        >
                          {Object.entries(data.factor_breakdown.momentum).map(([k, v]) => (
                            <div
                              key={k}
                              style={{
                                background: "var(--surface)",
                                padding: "var(--s-2)",
                                borderRadius: "var(--r-xs)",
                                textAlign: "center",
                              }}
                            >
                              <div style={{ fontSize: "10px", color: "var(--text-muted)", textTransform: "uppercase" }}>
                                {k.replace(/_/g, " ")}
                              </div>
                              <div style={{ fontSize: "var(--t-body)", fontWeight: 700, color: "var(--text-primary)" }}>
                                {v != null ? fmtNum(v, 2) : "—"}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Volatility & Sentiment */}
                    {(data.factor_breakdown.volatility_regime || data.factor_breakdown.sentiment) && (
                      <div
                        style={{
                          display: "grid",
                          gridTemplateColumns: "1fr 1fr",
                          gap: "var(--s-2)",
                          fontSize: "var(--t-caption)",
                        }}
                      >
                        {data.factor_breakdown.volatility_regime && (
                          <div
                            style={{
                              background: "var(--surface)",
                              padding: "var(--s-2)",
                              borderRadius: "var(--r-xs)",
                            }}
                          >
                            <span style={{ color: "var(--text-muted)" }}>Regime: </span>
                            <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>
                              {data.factor_breakdown.volatility_regime.regime != null
                                ? `Regime ${data.factor_breakdown.volatility_regime.regime}`
                                : "—"}
                            </span>
                          </div>
                        )}
                        {data.factor_breakdown.sentiment && (
                          <div
                            style={{
                              background: "var(--surface)",
                              padding: "var(--s-2)",
                              borderRadius: "var(--r-xs)",
                            }}
                          >
                            <span style={{ color: "var(--text-muted)" }}>Sentiment: </span>
                            <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>
                              {data.factor_breakdown.sentiment.aggregate_score != null
                                ? fmtNum(data.factor_breakdown.sentiment.aggregate_score, 2)
                                : "—"}
                            </span>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ) : (
                  <div
                    data-testid="no-signals-notice"
                    style={{
                      fontSize: "var(--t-caption)",
                      color: "var(--text-muted)",
                      padding: "var(--s-2)",
                      background: "var(--surface)",
                      borderRadius: "var(--r-xs)",
                      border: "1px dashed var(--border)",
                    }}
                  >
                    No daily signals computed for this cycle
                    {data.factor_breakdown.reason ? ` (${data.factor_breakdown.reason})` : ""}.
                  </div>
                )}
              </section>

              {/* Section 4: Price History & Recent Price Action */}
              <section
                data-testid="price-history-section"
                style={{
                  background: "var(--surface-2)",
                  padding: "var(--s-3)",
                  borderRadius: "var(--r-sm)",
                  border: "1px solid var(--border)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: "var(--s-2)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--s-2)",
                      fontSize: "var(--t-subhead)",
                      fontWeight: 600,
                      color: "var(--text-primary)",
                    }}
                  >
                    <TrendingUp size={16} />
                    <span>Recent Price Action</span>
                  </div>
                  <div style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
                    {data.price_history_status.bar_count} bars
                    {data.price_history_status.latest_close != null &&
                      ` · ${fmtUsd(data.price_history_status.latest_close)}`}
                  </div>
                </div>

                {barsLoading ? (
                  <Loading lines={3} />
                ) : data.price_history_status.available &&
                data.price_history_status.bar_count > 0 &&
                bars &&
                bars.length > 0 ? (
                  <div data-testid="price-chart">
                    <div style={{ width: "100%", height: 160 }}>
                      <ResponsiveContainer width="100%" height={160}>
                        <AreaChart
                          data={bars}
                          margin={{ top: 5, right: 10, left: 10, bottom: 0 }}
                        >
                          <defs>
                            <linearGradient id="priceGradient" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="5%" stopColor="var(--accent)" stopOpacity={0.3} />
                              <stop offset="95%" stopColor="var(--accent)" stopOpacity={0.0} />
                            </linearGradient>
                          </defs>
                          <XAxis dataKey="date" hide />
                          <YAxis domain={["auto", "auto"]} hide />
                          <Tooltip
                            contentStyle={{
                              background: "var(--surface-3)",
                              border: "1px solid var(--border)",
                              borderRadius: "4px",
                              fontSize: "12px",
                            }}
                            formatter={(value: any) => [fmtUsd(value), "Close"]}
                          />
                          <Area
                            type="monotone"
                            dataKey="Close"
                            stroke="var(--accent)"
                            strokeWidth={2}
                            fillOpacity={1}
                            fill="url(#priceGradient)"
                          />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                    {data.price_history_status.earliest_date && data.price_history_status.latest_date && (
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          fontSize: "10px",
                          color: "var(--text-muted)",
                          marginTop: "4px",
                        }}
                      >
                        <span>{data.price_history_status.earliest_date}</span>
                        <span>{data.price_history_status.latest_date}</span>
                      </div>
                    )}
                  </div>
                ) : (
                  <div
                    data-testid="no-price-bars-notice"
                    style={{
                      fontSize: "var(--t-caption)",
                      color: "var(--text-muted)",
                      padding: "var(--s-3)",
                      background: "var(--surface)",
                      borderRadius: "var(--r-xs)",
                      border: "1px dashed var(--border)",
                      textAlign: "center",
                    }}
                  >
                    <div style={{ fontWeight: 600, color: "var(--text-secondary)", marginBottom: "4px" }}>
                      No historical price bars available in local store
                    </div>
                    <div style={{ marginBottom: "var(--s-2)" }}>
                      {data.price_history_status.reason ||
                        "Historical price bars have not been backfilled for this symbol."}
                    </div>
                    <button
                      type="button"
                      className="btn btn-sm btn-secondary"
                      onClick={handleBackfill}
                      disabled={backfilling}
                      style={{ display: "inline-flex", alignItems: "center", gap: "var(--s-1)" }}
                    >
                      <RefreshCw size={12} className={backfilling ? "spin" : ""} />
                      <span>{backfilling ? "Backfilling..." : "Trigger Bar Backfill"}</span>
                    </button>
                    {backfillResult && (
                      <div style={{ marginTop: "4px", fontSize: "11px", color: "var(--accent)" }}>
                        {backfillResult}
                      </div>
                    )}
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </div>
    </>
  );
};

export default ExplainTickerDrawer;
