import { useState, useMemo } from "react";
import { Modal } from "./Modal";
import { Button } from "./ui";
import { Toggle } from "./Toggle";
import { CopyCommandBlock } from "./CopyCommandBlock";
import { RunCommandControl } from "./RunCommandControl";
import type { CommandSpec, CommandOption } from "../api/types";
import { REGISTERED_STRATEGIES } from "../commandParse";
import { theme } from "../theme";

interface CommandFormBuilderProps {
  command: CommandSpec | null;
  onClose: () => void;
  strategyRegistry?: string[];
}

export function CommandFormBuilder({ command, onClose, strategyRegistry = [] }: CommandFormBuilderProps) {
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

  // The subcommand spec (if any) as its own value, separate from activeSpec --
  // RunCommandControl needs the parent command + subcommand threaded as two
  // distinct params (matching commandParse.ts's parseCommandLine contract),
  // not bundled together the way activeSpec is for rendering the form.
  const subcommandSpec = useMemo<CommandSpec | null>(() => {
    if (command.subcommands.length === 0) return null;
    return command.subcommands.find((s) => s.name === selectedSubcommandName) ?? null;
  }, [command, selectedSubcommandName]);

  // The live registry passed in by Commands.tsx wins when present; otherwise
  // fall back to the hardcoded commandParse.ts list so this component works
  // correctly whether or not the caller threads the prop through.
  const effectiveStrategies = useMemo(
    () => (strategyRegistry.length > 0 ? strategyRegistry : REGISTERED_STRATEGIES),
    [strategyRegistry]
  );

  // Form values state: flag alias -> string | boolean
  const [optionValues, setOptionValues] = useState<Record<string, string | boolean>>(() => {
    const initial: Record<string, string | boolean> = {};
    for (const opt of activeSpec.options) {
      if (!opt.takes_value) {
        initial[opt.name] = Boolean(opt.default);
      } else if (opt.default !== null && opt.default !== undefined) {
        initial[opt.name] = String(opt.default);
      } else if (opt.name === "--strategies") {
        // refresh_validations.py's plural --strategies defaults to "the
        // whole registry" when omitted -- make that default explicit and
        // visible in the form instead of leaving it silently blank.
        initial[opt.name] = effectiveStrategies.join(",");
      } else {
        initial[opt.name] = "";
      }
    }
    return initial;
  });

  const handleOptionChange = (name: string, value: string | boolean) => {
    setOptionValues((prev) => ({ ...prev, [name]: value }));
  };

  // Compile argTokens and composed string from form state. The subcommand
  // name (if any) is deliberately NOT included in argTokens -- it's threaded
  // separately via subcommandSpec, matching commandParse.ts's parseCommandLine
  // contract (RunCommandControl sends `subcommand` as its own job param, not
  // folded into `args`).
  const { argTokens, composed } = useMemo(() => {
    const tokens: string[] = [];

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

    const fullComposed = [activeSpec.invocation, ...tokens].join(" ").trim();
    return { argTokens: tokens, composed: fullComposed };
  }, [activeSpec, optionValues]);

  return (
    <Modal ariaLabel={`Form Builder: ${command.name}`} onClose={onClose} size="wide">
      <div data-testid="command-form-builder">
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
            <label htmlFor="form-builder-subcommand" style={{ display: "block", fontSize: "var(--t-caption)", fontWeight: 600, color: theme.textSecondary, marginBottom: "var(--s-1)" }}>
              Select Subcommand
            </label>
            <select
              id="form-builder-subcommand"
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
                  strategyRegistry={effectiveStrategies}
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
                  else if (opt.name === "--strategies") reset[opt.name] = effectiveStrategies.join(",");
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
            </div>
          </div>

          {/* Run control -- reuses the exact same job-creation/toast/status/log
              logic as the free-text Command Bar's Run button, so this path
              can't silently drift back into a no-op. Deliberately does NOT
              call onClose() on launch: the operator needs to watch the job
              status/log right here; they dismiss manually via Close above
              once done. (If they close early, the job keeps running
              server-side -- same as navigating away from the Command Bar
              mid-run today.) */}
          <div style={{ marginTop: "var(--s-4)" }}>
            <RunCommandControl
              command={command}
              subcommand={subcommandSpec}
              argTokens={argTokens}
              disabled={false}
              composed={composed}
              resetKey={composed}
            />
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
  strategyRegistry,
}: {
  option: CommandOption;
  value: string | boolean | undefined;
  onChange: (val: string | boolean) => void;
  strategyRegistry: string[];
}) {
  // Exact-name matches only -- NOT a loose `.includes("strategy")` check.
  // validation.harness's singular --strategy (one value, a <select>) and
  // scripts/refresh_validations.py's plural --strategies (a comma-separated
  // list, a real multi-select) need genuinely different controls; a substring
  // match would leak the multi-select's "pre-select everything" default onto
  // the singular flag too.
  const isSingleStrategy = option.name === "--strategy";
  const isMultiStrategy = option.name === "--strategies";
  const isDate = option.name.includes("start") || option.name.includes("end") || option.name.includes("date");
  const choices = option.choices && option.choices.length > 0 ? option.choices : isSingleStrategy ? strategyRegistry : null;

  if (!option.takes_value) {
    return (
      <div style={{ background: theme.surface, padding: "var(--s-2-5) var(--s-3)", borderRadius: "var(--r-sm)", border: `1px solid ${theme.border}` }}>
        <Toggle
          checked={Boolean(value)}
          onChange={(checked) => onChange(checked)}
          label={option.name}
        />
        {option.description && (
          <div style={{ fontSize: "var(--t-caption)", color: theme.textMuted, marginTop: "var(--s-1)" }}>
            {option.description}
          </div>
        )}
      </div>
    );
  }

  if (isMultiStrategy) {
    return (
      <StrategyMultiSelectControl
        option={option}
        value={value}
        onChange={onChange}
        strategies={strategyRegistry}
      />
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

/**
 * Multi-select control for scripts/refresh_validations.py's plural
 * `--strategies` -- a comma-separated list of strategy names, or omitted
 * entirely to validate the whole registry (mirrors gui/panels/validation_lab.py's
 * `st.multiselect(..., default=options)` for the Streamlit "Validation Lab"
 * tab's equivalent control). Defaults to every strategy selected; "Select
 * All"/"Clear" plus a per-name Toggle let the operator narrow it down for a
 * real bulk validation run.
 */
function StrategyMultiSelectControl({
  option,
  value,
  onChange,
  strategies,
}: {
  option: CommandOption;
  value: string | boolean | undefined;
  onChange: (val: string | boolean) => void;
  strategies: string[];
}) {
  const selected = useMemo(() => {
    const raw = typeof value === "string" ? value : "";
    return new Set(
      raw
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s !== "")
    );
  }, [value]);

  const applySelection = (next: Set<string>) => {
    onChange([...next].sort().join(","));
  };

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

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--s-1-5)", gap: "var(--s-2)" }}>
        <div style={{ display: "flex", gap: "var(--s-2)" }}>
          <Button variant="neutral" onClick={() => applySelection(new Set(strategies))}>
            Select All
          </Button>
          <Button variant="neutral" onClick={() => applySelection(new Set())}>
            Clear
          </Button>
        </div>
        <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted, whiteSpace: "nowrap" }}>
          {selected.size} of {strategies.length} selected
        </span>
      </div>

      <div style={{ maxHeight: 200, overflowY: "auto", display: "flex", flexDirection: "column", gap: "var(--s-1-5)", paddingRight: "var(--s-1)" }}>
        {strategies.map((name) => (
          <Toggle
            key={name}
            checked={selected.has(name)}
            onChange={(checked) => {
              const next = new Set(selected);
              if (checked) {
                next.add(name);
              } else {
                next.delete(name);
              }
              applySelection(next);
            }}
            label={name}
          />
        ))}
      </div>
    </div>
  );
}
