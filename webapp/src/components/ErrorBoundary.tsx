import { Component, type ErrorInfo, type ReactNode } from "react";
import { theme } from "../theme";
import { Button } from "./ui";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Top-level render-error catch. Without this, ANY uncaught exception thrown
 * during render anywhere in the tree unmounts the entire app -- the page's
 * own near-black `--base` background then reads as a dead, unresponsive
 * screen, since nothing is left mounted to click. (This is exactly how the
 * Pipeline screen's `data.run_history` crash -- see PipelineDashboard.tsx --
 * presented before it was fixed at the source: no error, no stack trace
 * visible to the operator, just black.)
 *
 * This does not replace fixing the underlying bug (React error boundaries
 * cannot catch errors in event handlers, async code, or SSR, and swallowing
 * a real defect behind a generic screen is worse than not having one) -- it
 * bounds the blast radius of the NEXT one, the same "dead-letter, don't
 * crash" posture this codebase already applies to the pipeline itself.
 *
 * App.tsx keys this by route (`location.pathname`) so navigating to a
 * different screen remounts a fresh tree instead of leaving the operator
 * stuck on the crash -- consistent with every screen's own "‹ Back" link.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("Uncaught render error:", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="screen" data-testid="app-error-boundary">
        <h1 className="screen-title">Something went wrong</h1>
        <p className="screen-sub">
          This screen hit an unexpected error and couldn't render. The rest
          of the app is unaffected -- go back or pick another screen.
        </p>
        <section className="card card-pad" style={{ marginTop: "var(--s-4)" }}>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              color: theme.textMuted,
              fontSize: "var(--t-caption)",
              margin: 0,
              fontFamily: "monospace",
            }}
          >
            {error.message}
          </pre>
        </section>
        <div style={{ marginTop: "var(--s-4)" }}>
          <Button variant="primary" onClick={() => (window.location.href = "/")}>
            Back to Dashboard
          </Button>
        </div>
      </div>
    );
  }
}
