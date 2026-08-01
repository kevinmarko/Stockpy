import React, { createContext, useContext, useState, useCallback } from "react";

export type ToastType = "info" | "success" | "warning" | "error";

export interface ToastMessage {
  id: string;
  type: ToastType;
  title: string;
  description?: string;
}

interface ToastContextValue {
  toasts: ToastMessage[];
  addToast: (toast: Omit<ToastMessage, "id">) => void;
  removeToast: (id: string) => void;
}

const ToastContext = createContext<ToastContextValue | undefined>(undefined);

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback((toast: Omit<ToastMessage, "id">) => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts((prev) => [...prev, { ...toast, id }]);
    setTimeout(() => {
      removeToast(id);
    }, 4000);
  }, [removeToast]);

  return (
    <ToastContext.Provider value={{ toasts, addToast, removeToast }}>
      {children}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="false"
        style={{
          position: "fixed",
          top: "var(--s-4)",
          right: "var(--s-4)",
          zIndex: 9999,
          display: "flex",
          flexDirection: "column",
          gap: "var(--s-2)",
          maxWidth: "min(90vw, 360px)",
          pointerEvents: "none",
        }}
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            style={{
              pointerEvents: "auto",
              padding: "var(--s-2-5) var(--s-3-5)",
              borderRadius: "var(--r-md)",
              background: "var(--surface-2)",
              border: `1px solid ${
                t.type === "error"
                  ? "var(--decline)"
                  : t.type === "warning"
                  ? "var(--caution)"
                  : t.type === "success"
                  ? "var(--growth)"
                  : "var(--accent)"
              }`,
              boxShadow: "0 4px 20px rgba(0, 0, 0, 0.4)",
              color: "var(--text-primary)",
              fontSize: "var(--t-body)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "flex-start",
              gap: "var(--s-2)",
              animation: "slideIn 0.2s ease-out",
            }}
          >
            <div>
              <div style={{ fontWeight: 600, fontSize: "var(--t-callout)" }}>{t.title}</div>
              {t.description && (
                <div style={{ color: "var(--text-secondary)", fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
                  {t.description}
                </div>
              )}
            </div>
            <button
              onClick={() => removeToast(t.id)}
              style={{
                background: "none",
                border: "none",
                color: "var(--text-muted)",
                cursor: "pointer",
                padding: "0 0 0 var(--s-2)",
                fontSize: "var(--t-callout)",
                lineHeight: 1,
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
};

const dummyToastContext: ToastContextValue = {
  toasts: [],
  addToast: () => {},
  removeToast: () => {},
};

export const useToast = () => {
  const ctx = useContext(ToastContext);
  return ctx || dummyToastContext;
};
