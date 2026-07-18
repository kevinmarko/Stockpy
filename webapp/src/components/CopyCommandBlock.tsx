import { useEffect, useState, type ReactNode } from "react";
import { Button } from "./ui";
import { theme } from "../theme";

/**
 * CopyCommandBlock — a monospace command display plus a Copy/Copied button
 * that writes `command` to the clipboard. Lifted out of Commands.tsx's CLI
 * command bar (the "composed command + copy" block) because the Agentic
 * Trading tab's Discovery section needs the identical pattern for copying a
 * Claude Code skill-invocation prompt.
 *
 * The "Copied" indicator does NOT auto-revert on a timer — it stays until
 * `resetKey` changes, matching Commands.tsx's original behavior of clearing
 * it on every edit to the command bar's raw input (not merely when the
 * composed command text itself changes, e.g. a trailing space that doesn't
 * alter parsing still cleared it). Callers that don't need that granularity
 * can omit `resetKey`; it then falls back to `command`.
 */
export function CopyCommandBlock({
  command,
  label,
  disabled = false,
  resetKey,
  testIdPrefix = "command",
}: {
  command: string;
  label?: ReactNode;
  disabled?: boolean;
  resetKey?: unknown;
  testIdPrefix?: string;
}) {
  const [copied, setCopied] = useState(false);
  const effectiveResetKey = resetKey ?? command;

  useEffect(() => {
    setCopied(false);
  }, [effectiveResetKey]);

  const copy = () => {
    if (disabled || !command) return;
    void navigator.clipboard?.writeText(command);
    setCopied(true);
  };

  return (
    <div>
      {label !== undefined && (
        <div className="tile-label" style={{ marginBottom: 6 }}>
          {label}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, alignItems: "stretch" }}>
        <code
          data-testid={`${testIdPrefix}-composed`}
          style={{
            flex: 1,
            padding: "10px 12px",
            background: theme.surface,
            border: `1px solid ${theme.border}`,
            borderRadius: 8,
            fontFamily: "var(--font-mono, ui-monospace, monospace)",
            color: theme.textPrimary,
            overflowX: "auto",
            whiteSpace: "pre",
          }}
        >
          {command}
        </code>
        <Button onClick={copy} disabled={disabled} data-testid={`${testIdPrefix}-copy`}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}
