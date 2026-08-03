import { theme } from "../theme";
// Type-only import: erased at compile time, so this component still has ZERO
// runtime dependencies beyond ../theme (see the note below).
import type { EnvIssue } from "../config/env";

/**
 * EnvError — shown INSTEAD of the whole app when `config/env.ts` found an
 * error-severity problem in the `VITE_*` settings.
 *
 * Deliberately dependency-free: it imports only the theme tokens (plus an
 * erased type). It must NOT import api/client, the router, or any context
 * provider — the entire point is that it stays renderable when the app's
 * configuration is broken, and importing api/client would drag in the very
 * module whose config just failed to validate.
 *
 * This is NOT a duplicate of components/ErrorBoundary.tsx. That boundary
 * catches render/lifecycle errors inside a mounted route. Bad env is detected
 * during MODULE EVALUATION, before any component renders — React error
 * boundaries never see module-eval errors — so this is a separate top-level
 * branch in main.tsx rather than something the boundary could handle.
 *
 * There is intentionally NO retry button: Vite reads `.env` files only at
 * startup, so reloading the page cannot pick up a fix. A button that appeared
 * to retry but could never succeed would be dishonest.
 */
export function EnvError({ issues }: { issues: EnvIssue[] }) {
  const errors = issues.filter((i) => i.severity === "error");
  const warnings = issues.filter((i) => i.severity === "warning");

  return (
    <div
      style={{
        minHeight: "100vh",
        background: theme.base,
        color: theme.textPrimary,
        display: "flex",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div style={{ maxWidth: 640, width: "100%" }}>
        <h1
          style={{
            fontSize: 20,
            fontWeight: 600,
            margin: "8px 0 4px",
            color: theme.decline,
          }}
        >
          Configuration error
        </h1>
        <p
          style={{
            color: theme.textSecondary,
            fontSize: 14,
            lineHeight: 1.5,
            margin: "0 0 20px",
          }}
        >
          Stockpy Pilots did not start because one or more <code>VITE_*</code>{" "}
          settings could not be validated. The app refuses to guess at these
          rather than risk running against the wrong backend — or silently
          showing mock data as if it were real.
        </p>

        {errors.map((issue, idx) => (
          <div
            key={`${issue.key}-${idx}`}
            style={{
              background: theme.surface,
              border: `1px solid ${theme.decline}`,
              borderLeft: `4px solid ${theme.decline}`,
              borderRadius: 8,
              padding: "12px 14px",
              marginBottom: 12,
            }}
          >
            <div
              style={{
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                fontSize: 13,
                fontWeight: 600,
                color: theme.decline,
                marginBottom: 6,
              }}
            >
              {issue.key}
            </div>
            <div
              style={{
                fontSize: 14,
                lineHeight: 1.5,
                color: theme.textPrimary,
              }}
            >
              {issue.message}
            </div>
          </div>
        ))}

        {warnings.length > 0 && (
          <div style={{ marginTop: 20 }}>
            <h2
              style={{
                fontSize: 13,
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                color: theme.textSecondary,
                margin: "0 0 10px",
              }}
            >
              Also worth checking
            </h2>
            {warnings.map((issue, idx) => (
              <div
                key={`${issue.key}-${idx}`}
                style={{
                  background: theme.surface,
                  border: `1px solid ${theme.border}`,
                  borderLeft: `4px solid ${theme.caution}`,
                  borderRadius: 8,
                  padding: "10px 14px",
                  marginBottom: 10,
                }}
              >
                <div
                  style={{
                    fontFamily:
                      "ui-monospace, SFMono-Regular, Menlo, monospace",
                    fontSize: 12,
                    fontWeight: 600,
                    color: theme.caution,
                    marginBottom: 4,
                  }}
                >
                  {issue.key}
                </div>
                <div
                  style={{
                    fontSize: 13,
                    lineHeight: 1.5,
                    color: theme.textSecondary,
                  }}
                >
                  {issue.message}
                </div>
              </div>
            ))}
          </div>
        )}

        <div
          style={{
            marginTop: 24,
            paddingTop: 16,
            borderTop: `1px solid ${theme.border}`,
            fontSize: 14,
            lineHeight: 1.6,
            color: theme.textSecondary,
          }}
        >
          <strong style={{ color: theme.textPrimary }}>How to fix</strong>
          <ol style={{ margin: "8px 0 0", paddingLeft: 20 }}>
            <li>
              Edit <code>webapp/.env.local</code> (see{" "}
              <code>webapp/.env.example</code> for every supported key and its
              default).
            </li>
            <li>
              Restart the dev server (<code>npm run dev</code>) — or rebuild, if
              you are serving a production build.
            </li>
          </ol>
          <p style={{ margin: "12px 0 0" }}>
            Reloading this page will not help: Vite reads <code>.env</code>{" "}
            files only at startup, so the process has to restart to pick up your
            change.
          </p>
        </div>
      </div>
    </div>
  );
}
