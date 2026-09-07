import React from "react";
import { Info } from "lucide-react";
import { useExplainTicker } from "../context/ExplainTickerContext";

export interface ExplainTickerButtonProps {
  symbol: string;
  className?: string;
  size?: number;
  title?: string;
}

/**
 * Reusable (ⓘ) button that triggers the "Explain This Ticker" slide-over drawer.
 *
 * Calls e.preventDefault() and e.stopPropagation() to safely coexist inside
 * table rows, card links (<Link to="...">), or button containers without
 * triggering unintended navigation or row selection.
 */
export const ExplainTickerButton: React.FC<ExplainTickerButtonProps> = ({
  symbol,
  className = "",
  size = 13,
  title,
}) => {
  const { openExplainTicker } = useExplainTicker();

  if (!symbol) return null;

  const label = title ?? `Explain ${symbol}`;

  return (
    <button
      type="button"
      className={`explain-ticker-btn ${className}`}
      aria-label={label}
      title={label}
      data-testid={`explain-ticker-btn-${symbol}`}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        openExplainTicker(symbol);
      }}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        background: "transparent",
        border: "none",
        padding: "2px 4px",
        margin: "0 2px",
        cursor: "pointer",
        color: "var(--text-muted)",
        borderRadius: "4px",
        verticalAlign: "middle",
        lineHeight: 1,
        transition: "color 0.15s ease, background 0.15s ease",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = "var(--text-primary)";
        e.currentTarget.style.background = "var(--surface-2)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = "var(--text-muted)";
        e.currentTarget.style.background = "transparent";
      }}
    >
      <Info size={size} aria-hidden="true" />
    </button>
  );
};

export const TickerWithExplain: React.FC<{
  symbol: string;
  children?: React.ReactNode;
  className?: string;
}> = ({ symbol, children, className = "" }) => {
  return (
    <span
      className={`ticker-with-explain ${className}`}
      style={{ display: "inline-flex", alignItems: "center", gap: "2px" }}
    >
      {children ?? <span>{symbol}</span>}
      <ExplainTickerButton symbol={symbol} />
    </span>
  );
};

export default ExplainTickerButton;
