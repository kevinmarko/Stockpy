import { useState, useMemo } from "react";
import { Modal } from "./Modal";
import { Button, Toggle } from "./ui";
import { CopyCommandBlock } from "./CopyCommandBlock";
import type { CommandSpec, CommandOption } from "../api/types";
import { REGISTERED_STRATEGIES } from "../commandParse";
import { theme } from "../theme";

interface CommandFormBuilderProps {
  command: CommandSpec | null;
  onClose: () => void;
  onRunCommand?: (composed: string, spec: CommandSpec, argTokens: string[]) => void;
}

export function CommandFormBuilder({ command, onClose, onRunCommand }: CommandFormBuilderProps) {
  if (!command) return null;

  const [selectedSubcommandName, setSelectedSubcommandName] = useState<string>(
    command.subcommands.length > 0 ? command.subcommands[0].name : ""
  );

  const activeSpec = useMemo(() => {
    if (command.subcommands.length > 0) {
      return command.subcommands.find((s) => s.name === selectedSubcommandName) ?? command;
    }
    return command;
  }, [command, selectedSubcommandName]);

  // Form values state: flag alias -> string | boolean
  const [optionValues, setOptionValues] = useState<Record<string, string | boolean>>(() => {
    const initial: Record<string, string | boolean> = {};
    for (const opt of activeSpec.options) {
      if (!opt.takes_value) {
        initial[opt.name] = Boolean(opt.default);
      } else if (opt.default !== null && opt.default !== undefined) {
        initial[opt.name] = String(opt.default);
      } else {
        initial[opt.name] = "";
      }
    }
    return initial;
  });

  const handleOptionChange = (name: string, value: string | boolean) => {
    setOptionValues((prev) => ({ ...prev, [name]: value }));
  };

  // Compile argTokens and composed string from form state
  const { argTokens, composed } = useMemo(() => {
    const tokens: string[] = [];
    if (command.subcommands.length > 0 && selectedSubcommandName) {
      tokens.push(selectedSubcommandName);
    }

    for (const opt of activeSpec.options) {
      const val = optionValues[opt.name];
      if (!opt.takes_value) {
        if (val === true) {
          tokens.push(opt.name);
        }
      } else if (val !== undefined && val !== null && String(val).trim() !== "") {
        tokens.push(opt.name);
        tokens.push(String(val).trim());
      }
    }

    const fullComposed = [activeSpec.invocation, ...tokens.slice(command.subcommands.length > 0 ? 1 : 0)].join(" ");
    return { argTokens: tokens, composed: fullComposed };
  }, [command, selectedSubcommandName, activeSpec, optionValues]);

  return (
    <Modal ariaLabel={`Form Builder: ${command.name}`} onClose={onClose}>
      <div style={{ minWidth: 540, maxWidth: 720 }} data-testid="command-form-builder">
        {/* Title / Subhead */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--s-3)" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
              <span style={{ fontSize: "1.2rem" }}>🛠️</span>
              <h2 style={{ margin: 0, fontSize: "var(--t-subhead)", color: theme.textPrimary }}>
                {command.name}
              </h2>
            </div>
            {command.description && (
              <p style={{ margin: "var(--s-1) 0 0", fontSize: "var(--t-caption)", color: theme.textMuted }}>
                {command.description}
              </p>
            )}
          </div>
          <span
            style={{
              fontSize: "var(--t-micro)",
              fontFamily: "var(--font-mono, ui-monospace, monospace)",
              padding: "2px 8px",
              background: theme.surface2,
              borderRadius: "var(--r-sm)",
              color: theme.textSecondary,
            }}
          >
            Form Mode
          </span>
        </div>

        {/* Subcommand selector if command has subcommands */}
        {command.subcommands.length > 0 && (
          <div style={{ marginBottom: "var(--s-4)", background: theme.surface2, padding: "var(--s-3)", borderRadius: "var(--r-sm)" }}>
            <label style={{ display: "block", fontSize: "var(--t-caption)", fontWeight: 600, color: theme.textSecondary, marginBottom: "var(--s-1)" }}>
              Select Subcommand
            </label>
            <select
              className="input"
              value={selectedSubcommandName}
              onChange={(e) => setSelectedSubcommandName(e.target.value)}
              style={{ width: "100%" }}
            >
              {command.subcommands.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name} {s.description ? `— ${s.description}` : ""}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Dynamic Form Controls for Options */}
        <div style={{ maxHeight: 360, overflowY: "auto", paddingRight: "var(--s-1)", marginBottom: "var(--s-4)" }}>
          {activeSpec.options.length === 0 ? (
            <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", fontStyle: "italic" }}>
              This command takes no additional flags or parameters.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3-5)" }}>
              {activeSpec.options.map((opt) => (
                <OptionFormControl
                  key={opt.name}
                  option={opt}
                  value={optionValues[opt.name]}
                  onChange={(val) => handleOptionChange(opt.name, val)}
                />
              ))}
            </div>
          )}
        </div>

        {/* Compiled Output Preview & Action Bar */}
        <div style={{ borderTop: `1px solid ${theme.border}`, paddingTop: "var(--s-3)" }}>
          <CopyCommandBlock command={composed} label="Compiled Execution Target" />

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "var(--s-3)" }}>
            <Button
              variant="neutral"
              onClick={() => {
                // Reset to defaults
                const reset: Record<string, string | boolean> = {};
                for (const opt of activeSpec.options) {
                  if (!opt.takes_value) reset[opt.name] = Boolean(opt.default);
                  else if (opt.default !== null && opt.default !== undefined) reset[opt.name] = String(opt.default);
                  else reset[opt.name] = "";
                }
                setOptionValues(reset);
              }}
            >
              Reset
            </Button>

            <div style={{ display: "flex", gap: "var(--s-2)" }}>
              <Button variant="neutral" onClick={onClose}>
                Close
              </Button>
              {onRunCommand && (
                <Button
                  variant="primary"
                  onClick={() => {
                    onRunCommand(composed, command, argTokens);
                    onClose();
                  }}
                  data-testid="form-builder-run-button"
                >
                  Execute Command 🚀
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
}

function OptionFormControl({
  option,
  value,
  onChange,
}: {
  option: CommandOption;
  value: string | boolean | undefined;
  onChange: (val: string | boolean) => void;
}) {
  const isStrategy = option.name.includes("strategy");
  const isDate = option.name.includes("start") || option.name.includes("end") || option.name.includes("date");
  const choices = option.choices && option.choices.length > 0 ? option.choices : isStrategy ? REGISTERED_STRATEGIES : null;

  if (!option.takes_value) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: theme.surface, padding: "var(--s-2-5) var(--s-3)", borderRadius: "var(--r-sm)", border: `1px solid ${theme.border}` }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontWeight: 600, color: theme.textPrimary, fontSize: "var(--t-body)" }}>
            {option.name}
          </div>
          {option.description && (
            <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted, marginTop: 2 }}>
              {option.description}
            </div>
          )}
        </div>
        <Toggle
          checked={Boolean(value)}
          onChange={(checked) => onChange(checked)}
          ariaLabel={option.name}
        />
      </div>
    );
  }

  return (
    <div style={{ background: theme.surface, padding: "var(--s-2-5) var(--s-3)", borderRadius: "var(--r-sm)", border: `1px solid ${theme.border}` }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: "var(--s-1)" }}>
        <label style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontWeight: 600, color: theme.textPrimary, fontSize: "var(--t-body)" }}>
          {option.name}
          {option.required && <span style={{ color: theme.decline, marginLeft: 4 }}>*</span>}
        </label>
        {option.default !== null && option.default !== undefined && (
          <span style={{ fontSize: "var(--t-micro)", color: theme.textMuted }}>
            Default: {String(option.default)}
          </span>
        )}
      </div>

      {option.description && (
        <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted, marginBottom: "var(--s-1-5)" }}>
          {option.description}
        </div>
      )}

      {choices ? (
        <select
          className="input"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          style={{ width: "100%", fontFamily: "var(--font-mono, ui-monospace, monospace)" }}
        >
          <option value="">-- Select {option.name} --</option>
          {choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : isDate ? (
        <input
          type="date"
          className="input"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          style={{ width: "100%", fontFamily: "var(--font-mono, ui-monospace, monospace)" }}
        />
      ) : (
        <input
          type="text"
          className="input"
          placeholder={option.metavar ? `<${option.metavar}>` : "Enter value..."}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          style={{ width: "100%", fontFamily: "var(--font-mono, ui-monospace, monospace)" }}
        />
      )}
    </div>
  );
}
