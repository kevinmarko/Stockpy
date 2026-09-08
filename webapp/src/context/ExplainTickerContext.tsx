import React, { createContext, useCallback, useContext, useState } from "react";

/**
 * Global context for "Explain This Ticker" slide-over drawer.
 *
 * Lets any ticker link or (ⓘ) info icon across any screen under React Router
 * open the global ExplainTickerDrawer without threading state or callbacks.
 * Follows the ChatContext / ToastContext safe-fallback pattern so calling
 * useExplainTicker() outside the provider safely returns a no-op context.
 */

export interface ExplainTickerContextValue {
  isOpen: boolean;
  symbol: string | null;
  openExplainTicker: (symbol: string) => void;
  closeExplainTicker: () => void;
}

const ExplainTickerContext = createContext<ExplainTickerContextValue | undefined>(undefined);

export const ExplainTickerProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [symbol, setSymbol] = useState<string | null>(null);
  const [isOpen, setIsOpen] = useState(false);

  const openExplainTicker = useCallback((sym: string) => {
    if (!sym) return;
    setSymbol(sym.trim().toUpperCase());
    setIsOpen(true);
  }, []);

  const closeExplainTicker = useCallback(() => {
    setIsOpen(false);
  }, []);

  return (
    <ExplainTickerContext.Provider
      value={{
        isOpen,
        symbol,
        openExplainTicker,
        closeExplainTicker,
      }}
    >
      {children}
    </ExplainTickerContext.Provider>
  );
};

const dummyExplainTickerContext: ExplainTickerContextValue = {
  isOpen: false,
  symbol: null,
  openExplainTicker: () => {},
  closeExplainTicker: () => {},
};

export function useExplainTicker(): ExplainTickerContextValue {
  const ctx = useContext(ExplainTickerContext);
  return ctx ?? dummyExplainTickerContext;
}
