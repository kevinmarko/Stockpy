/**
 * commandParse.ts — the framework-agnostic engine behind the command bar.
 *
 * Given the raw text the operator has typed and the command manifest (from GET
 * /commands), it resolves which command/subcommand is being invoked, produces
 * ranked autocomplete suggestions for the token under the cursor, and emits
 * pre-execution validation hints (missing required arg, unknown option) — the
 * four functional specs, all client-side and pure so they're unit-testable in
 * isolation.
 *
 * `composed` can now also be RUN directly (a gated, confirmed execution path —
 * see Commands.tsx's Run control and the backend's `COMMAND_EXECUTION_ENABLED`
 * flag), not just copied. High-stakes commands (the kill switch, a forced
 * broker re-login) require an explicit operator confirmation
 * (`highStakesReason`) before the run request is even sent; `app_shell.py`
 * (`DISALLOWED_EXECUTE_COMMANDS`) stays copy-only since it opens a native
 * window on the server host, not the browser.
 */
import type { CommandSpec, CommandOption } from "./api/types";

export type SuggestionKind = "command" | "subcommand" | "option" | "value";

export interface Suggestion {
  /** Token text inserted when the suggestion is accepted. */
  value: string;
  /** Display label (value, plus metavar for value-taking options). */
  label: string;
  /** Help text: description, default, and/or choices. */
  description: string;
  kind: SuggestionKind;
}

export interface ValidationHint {
  level: "error" | "warn";
  message: string;
}

export interface ParseResult {
  command: CommandSpec | null; // resolved top-level command
  subcommand: CommandSpec | null; // resolved subcommand (if the command has any)
  /** The active spec whose options/positionals apply (command or subcommand). */
  active: CommandSpec | null;
  suggestions: Suggestion[];
  hints: ValidationHint[];
  /** Full CLI string to copy/run, or null until a runnable command is resolved. */
  composed: string | null;
  /** The exact tokens used to build `composed` (empty until one is resolved). */
  argTokens: string[];
}

/** Mirrors gui/orchestrator_runner.py's HIGH_STAKES_COMMANDS table exactly —
 *  same two command names, same flag names. This is a client-side UX hint
 *  only; the server is still the enforcing authority. */
const HIGH_STAKES_COMMANDS: Record<string, { flags: string[]; reason: string }[]> = {
  "execution.kill_switch": [
    { flags: ["--activate"], reason: "This activates the platform's global kill switch — it immediately blocks ALL order submission." },
    { flags: ["--deactivate"], reason: "This deactivates the platform's global kill switch — order submission resumes." },
  ],
  "main.py": [
    { flags: ["--refresh-account"], reason: "This forces a fresh Robinhood login, bypassing the daily account-snapshot cache." },
  ],
};

/** Non-null when running `command` with `argTokens` needs explicit operator
 *  confirmation before executing (kill switch activate/deactivate, a forced
 *  broker re-login). Mirrors gui/orchestrator_runner.py's HIGH_STAKES_COMMANDS
 *  table — the server is still the enforcing authority; this is a client-side
 *  UX gate so the operator sees the risk BEFORE the request is even sent. */
export function highStakesReason(command: CommandSpec | null, argTokens: string[]): string | null {
  if (!command) return null;
  const rules = HIGH_STAKES_COMMANDS[command.name];
  if (!rules) return null;
  const argSet = new Set(argTokens);
  for (const rule of rules) {
    if (rule.flags.every((f) => argSet.has(f))) return rule.reason;
  }
  return null;
}

/** app_shell.py pops a native desktop window on the server host — never
 *  executable from a browser click. Stays copy-only. */
export const DISALLOWED_EXECUTE_COMMANDS: ReadonlySet<string> = new Set(["app_shell.py"]);

/** Whitespace-tokenize, dropping empties. */
function tokenize(input: string): string[] {
  return input.split(/\s+/).filter(Boolean);
}

function lastInvToken(cmd: CommandSpec): string {
  const parts = cmd.invocation.split(/\s+/);
  return parts[parts.length - 1];
}

/** Every string that resolves to this command (for exact matching). */
function commandKeys(cmd: CommandSpec): string[] {
  return [cmd.name, ...cmd.aliases, lastInvToken(cmd)];
}

function resolveCommand(commands: CommandSpec[], token: string): CommandSpec | null {
  const t = token.toLowerCase();
  return (
    commands.find((c) => commandKeys(c).some((k) => k.toLowerCase() === t)) ?? null
  );
}

/** Substring match on any command key — for suggestions while still typing. */
function matchCommands(commands: CommandSpec[], partial: string): CommandSpec[] {
  const t = partial.toLowerCase();
  if (!t) return commands;
  return commands.filter((c) => commandKeys(c).some((k) => k.toLowerCase().includes(t)));
}

function findOption(spec: CommandSpec, alias: string): CommandOption | null {
  return spec.options.find((o) => o.aliases.includes(alias)) ?? null;
}

function optionDescription(o: CommandOption): string {
  const bits: string[] = [];
  if (o.description) bits.push(o.description);
  if (o.required) bits.push("(required)");
  if (o.choices && o.choices.length) bits.push(`choices: ${o.choices.join(", ")}`);
  if (o.default !== null && o.default !== undefined && o.default !== false)
    bits.push(`default: ${o.default}`);
  return bits.join(" · ");
}

function commandSuggestions(commands: CommandSpec[], partial: string): Suggestion[] {
  return matchCommands(commands, partial).map((c) => ({
    value: c.name,
    label: c.name,
    description: c.description ?? "",
    kind: "command" as const,
  }));
}

function subcommandSuggestions(parent: CommandSpec, partial: string): Suggestion[] {
  const t = partial.toLowerCase();
  return parent.subcommands
    .filter((s) => !t || [s.name, ...s.aliases].some((k) => k.toLowerCase().includes(t)))
    .map((s) => ({
      value: s.name,
      label: s.aliases.length ? `${s.name} (${s.aliases.join(", ")})` : s.name,
      description: s.description ?? "",
      kind: "subcommand" as const,
    }));
}

function optionSuggestions(spec: CommandSpec, usedAliases: Set<string>, partial: string): Suggestion[] {
  const t = partial.toLowerCase();
  return spec.options
    .filter((o) => !o.aliases.some((a) => usedAliases.has(a))) // hide already-used flags
    .filter((o) => !t || o.aliases.some((a) => a.toLowerCase().includes(t)))
    .map((o) => ({
      value: o.name,
      label: o.metavar && o.takes_value ? `${o.name} <${o.metavar}>` : o.name,
      description: optionDescription(o),
      kind: "option" as const,
    }));
}

function valueSuggestions(option: CommandOption, partial: string): Suggestion[] {
  if (!option.choices) return [];
  const t = partial.toLowerCase();
  return option.choices
    .filter((c) => !t || c.toLowerCase().includes(t))
    .map((c) => ({ value: c, label: c, description: `value for ${option.name}`, kind: "value" as const }));
}

/**
 * Validate the settled argument tokens against the active spec: missing
 * required options/positionals, and unknown options. Approximate but honest —
 * it flags what it's sure about and stays quiet otherwise.
 */
function validate(spec: CommandSpec, argTokens: string[]): ValidationHint[] {
  const hints: ValidationHint[] = [];
  const usedAliases = new Set(argTokens.filter((t) => t.startsWith("-")));

  // Unknown options.
  for (const tok of argTokens) {
    if (tok.startsWith("-") && !findOption(spec, tok)) {
      hints.push({ level: "error", message: `unknown option: ${tok}` });
    }
  }

  // Missing required options.
  for (const o of spec.options) {
    if (o.required && !o.aliases.some((a) => usedAliases.has(a))) {
      hints.push({ level: "error", message: `missing required option: ${o.name}` });
    }
  }

  // Missing required positionals: count values that aren't flags and aren't
  // consumed as an option's value.
  let provided = 0;
  for (let i = 0; i < argTokens.length; i++) {
    const tok = argTokens[i];
    if (tok.startsWith("-")) continue;
    const prev = argTokens[i - 1];
    const prevOpt = prev && prev.startsWith("-") ? findOption(spec, prev) : null;
    if (prevOpt && prevOpt.takes_value) continue; // this token is the option's value
    provided += 1;
  }
  const requiredPositionals = spec.positionals.filter((p) => p.arg_kind === "required");
  for (let i = provided; i < requiredPositionals.length; i++) {
    hints.push({ level: "error", message: `missing required argument: ${requiredPositionals[i].name}` });
  }

  return hints;
}

export function parseCommandLine(input: string, commands: CommandSpec[]): ParseResult {
  const empty: ParseResult = {
    command: null,
    subcommand: null,
    active: null,
    suggestions: [],
    hints: [],
    composed: null,
    argTokens: [],
  };

  const tokens = tokenize(input);
  const typing = input.length > 0 && !/\s$/.test(input); // last token still being typed?
  const partial = typing ? tokens[tokens.length - 1] : "";
  // Index of the token currently being completed; `settled` are the tokens
  // before it (already committed).
  const completingIndex = typing ? tokens.length - 1 : tokens.length;
  const settled = tokens.slice(0, completingIndex);

  // ── Completing the command name itself ──────────────────────────────────
  if (settled.length === 0) {
    return { ...empty, suggestions: commandSuggestions(commands, partial) };
  }

  // ── Resolve the top-level command ───────────────────────────────────────
  const command = resolveCommand(commands, settled[0]);
  if (!command) {
    return {
      ...empty,
      suggestions: commandSuggestions(commands, partial),
      hints: [{ level: "error", message: `unknown command: ${settled[0]}` }],
    };
  }

  // ── Subcommand handling ─────────────────────────────────────────────────
  let subcommand: CommandSpec | null = null;
  let active: CommandSpec = command;
  let argStart = 1; // index in `tokens` where this spec's args begin

  if (command.subcommands.length > 0) {
    const subToken = settled[1];
    subcommand = subToken ? resolveCommand(command.subcommands, subToken) : null;
    if (subcommand) {
      active = subcommand;
      argStart = 2;
    } else {
      // Still choosing (or a bad) subcommand.
      const hints: ValidationHint[] =
        settled.length >= 2 && subToken
          ? [{ level: "error", message: `unknown subcommand: ${subToken}` }]
          : [
              {
                level: "warn",
                message: `choose a subcommand: ${command.subcommands.map((s) => s.name).join(", ")}`,
              },
            ];
      return {
        command,
        subcommand: null,
        active: null,
        suggestions: subcommandSuggestions(command, partial),
        hints,
        composed: null,
        argTokens: [],
      };
    }
  }

  // ── Option / value / positional context ─────────────────────────────────
  const settledArgs = settled.slice(argStart); // committed args only
  const usedAliases = new Set(settledArgs.filter((t) => t.startsWith("-")));

  let suggestions: Suggestion[];
  const prevToken = settled[settled.length - 1];
  const prevOption = prevToken && prevToken.startsWith("-") ? findOption(active, prevToken) : null;

  if (partial.startsWith("-")) {
    suggestions = optionSuggestions(active, usedAliases, partial);
  } else if (prevOption && prevOption.takes_value && prevOption.choices) {
    suggestions = valueSuggestions(prevOption, partial);
  } else if (prevOption && prevOption.takes_value) {
    suggestions = []; // free value expected (e.g. a date, a name)
  } else {
    // Positional / next-token context: offer the remaining options as guidance.
    suggestions = optionSuggestions(active, usedAliases, partial);
  }

  const hints = validate(active, settledArgs);
  const argTokens = tokens.slice(argStart);
  const composed = [active.invocation, ...argTokens].join(" ").trim();

  return { command, subcommand, active, suggestions, hints, composed, argTokens };
}
