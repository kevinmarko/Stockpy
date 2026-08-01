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

export type CommandCategory = "pipeline" | "testing" | "database" | "reporting";

export interface CategoryInfo {
  id: CommandCategory;
  label: string;
  icon: string;
  description: string;
}

export const CATEGORIES: CategoryInfo[] = [
  { id: "pipeline", label: "Pipeline & Core", icon: "🚀", description: "Master orchestrators and application shells" },
  { id: "testing", label: "Testing & Validation", icon: "🧪", description: "Strategy validation, preflight checks, and benchmarks" },
  { id: "database", label: "Database & Operations", icon: "🗄️", description: "Database migrations, kill switches, and prompt registry" },
  { id: "reporting", label: "Reporting & Analytics", icon: "📊", description: "Daily briefings, track record status, and HTML reports" },
];

export function getCommandCategory(name: string): CommandCategory {
  const n = name.toLowerCase();
  if (n.includes("main") || n.includes("app_shell") || n.includes("orchestrator")) return "pipeline";
  if (n.includes("validation") || n.includes("preflight") || n.includes("test")) return "testing";
  if (n.includes("database") || n.includes("kill_switch") || n.includes("prompt")) return "database";
  if (n.includes("briefing") || n.includes("track_record") || n.includes("report")) return "reporting";
  return "pipeline";
}

/** Fuzzy match score: returns positive score if `pattern` characters match in sequence inside `text`, or 0 if no match. Higher = better match. */
export function fuzzyScore(pattern: string, text: string): number {
  if (!pattern) return 1;
  const p = pattern.toLowerCase();
  const t = text.toLowerCase();
  if (t === p) return 1000;
  if (t.startsWith(p)) return 800;
  if (t.includes(p)) return 500;

  let pIdx = 0;
  let score = 0;
  let consecutive = 0;
  for (let i = 0; i < t.length && pIdx < p.length; i++) {
    if (t[i] === p[pIdx]) {
      pIdx++;
      consecutive++;
      score += 10 + consecutive * 5;
    } else {
      consecutive = 0;
    }
  }
  return pIdx === p.length ? score : 0;
}

export function fuzzyMatch(pattern: string, text: string): boolean {
  return fuzzyScore(pattern, text) > 0;
}

/**
 * Known strategy names for contextual autocompletion of --strategy option.
 * `validation/harness.py`'s `--strategy` argparse arg has no `choices=` (it
 * can't import `scripts.refresh_validations.STRATEGY_REGISTRY` without a
 * circular import — that module imports the harness), so the manifest never
 * carries real choices for this flag. This list must be kept in sync by hand
 * with the keys of `STRATEGY_REGISTRY` in `scripts/refresh_validations.py`.
 */
export const REGISTERED_STRATEGIES = [
  "rsi2_mean_reversion",
  "timeseries_momentum",
  "macd_trend",
  "coppock_momentum",
  "multifactor_lowvol_size",
  "garch_vol_target",
  "cross_sectional_momentum",
  "relative_strength_xsec",
  "rsi14_extremes",
  "sortino_drawdown",
  "dividend_yield_edgar_pit",
  "deep_value_edgar_pit",
  "value_quality_edgar_pit",
  "macro_regime_pit",
  "forecast_direction_arima_hw",
  "signal_replay_balanced_blend",
];

/** Substring or fuzzy match on any command key — for suggestions while still typing. */
function matchCommands(commands: CommandSpec[], partial: string): CommandSpec[] {
  if (!partial) return commands;
  return commands
    .map((c) => {
      const bestScore = Math.max(...commandKeys(c).map((k) => fuzzyScore(partial, k)));
      return { command: c, score: bestScore };
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score)
    .map((item) => item.command);
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
  if (!partial) {
    return parent.subcommands.map((s) => ({
      value: s.name,
      label: s.aliases.length ? `${s.name} (${s.aliases.join(", ")})` : s.name,
      description: s.description ?? "",
      kind: "subcommand" as const,
    }));
  }
  return parent.subcommands
    .map((s) => {
      const bestScore = Math.max(
        fuzzyScore(partial, s.name),
        ...s.aliases.map((a) => fuzzyScore(partial, a))
      );
      return { subcommand: s, score: bestScore };
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score)
    .map(({ subcommand: s }) => ({
      value: s.name,
      label: s.aliases.length ? `${s.name} (${s.aliases.join(", ")})` : s.name,
      description: s.description ?? "",
      kind: "subcommand" as const,
    }));
}

function optionSuggestions(spec: CommandSpec, usedAliases: Set<string>, partial: string): Suggestion[] {
  const availableOptions = spec.options.filter((o) => !o.aliases.some((a) => usedAliases.has(a)));
  if (!partial) {
    return availableOptions.map((o) => ({
      value: o.name,
      label: o.metavar && o.takes_value ? `${o.name} <${o.metavar}>` : o.name,
      description: optionDescription(o),
      kind: "option" as const,
    }));
  }
  return availableOptions
    .map((o) => {
      const bestScore = Math.max(...o.aliases.map((a) => fuzzyScore(partial, a)));
      return { option: o, score: bestScore };
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score)
    .map(({ option: o }) => ({
      value: o.name,
      label: o.metavar && o.takes_value ? `${o.name} <${o.metavar}>` : o.name,
      description: optionDescription(o),
      kind: "option" as const,
    }));
}

function valueSuggestions(option: CommandOption, partial: string): Suggestion[] {
  let choices = option.choices ?? [];
  if (choices.length === 0 && option.name.includes("strategy")) {
    choices = REGISTERED_STRATEGIES;
  }
  if (choices.length === 0 && (option.name.includes("start") || option.name.includes("end") || option.name.includes("date"))) {
    const currentYear = new Date().getFullYear();
    choices = [`${currentYear - 2}-01-01`, `${currentYear - 1}-01-01`, `${currentYear}-01-01`];
  }
  if (!choices || choices.length === 0) return [];

  if (!partial) {
    return choices.map((c) => ({
      value: c,
      label: c,
      description: `value for ${option.name}`,
      kind: "value" as const,
    }));
  }

  return choices
    .map((c) => ({ value: c, score: fuzzyScore(partial, c) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score)
    .map(({ value: c }) => ({
      value: c,
      label: c,
      description: `value for ${option.name}`,
      kind: "value" as const,
    }));
}

/** Computes ghost text suffix to display inline behind cursor if top suggestion matches typing */
export function getGhostText(input: string, suggestions: Suggestion[]): string {
  if (!input || !suggestions || suggestions.length === 0) return "";
  if (/\s$/.test(input)) return "";
  const tokens = tokenize(input);
  if (tokens.length === 0) return "";
  const lastToken = tokens[tokens.length - 1];
  const topMatch = suggestions[0].value;
  if (topMatch.toLowerCase().startsWith(lastToken.toLowerCase())) {
    return topMatch.slice(lastToken.length);
  }
  return "";
}

export interface HighlightToken {
  text: string;
  type: "interpreter" | "command" | "subcommand" | "option" | "value" | "flag" | "unknown";
}

export function tokenizeForHighlighting(input: string, commands: CommandSpec[]): HighlightToken[] {
  const result: HighlightToken[] = [];
  const parts = input.split(/(\s+)/);
  let resolvedCommand: CommandSpec | null = null;
  let resolvedSubcommand: CommandSpec | null = null;
  let expectingValueForOpt: CommandOption | null = null;

  for (const part of parts) {
    if (!part) continue;
    if (/^\s+$/.test(part)) {
      result.push({ text: part, type: "unknown" });
      continue;
    }

    if (!resolvedCommand) {
      if (part === "python" || part === "python3") {
        result.push({ text: part, type: "interpreter" });
        continue;
      }
      if (part === "-m") {
        result.push({ text: part, type: "flag" });
        continue;
      }
      const cmd = resolveCommand(commands, part);
      if (cmd) {
        resolvedCommand = cmd;
        result.push({ text: part, type: "command" });
        continue;
      }
    } else if (resolvedCommand.subcommands.length > 0 && !resolvedSubcommand) {
      const sub = resolveCommand(resolvedCommand.subcommands, part);
      if (sub) {
        resolvedSubcommand = sub;
        result.push({ text: part, type: "subcommand" });
        continue;
      }
    }

    const activeSpec = resolvedSubcommand ?? resolvedCommand;
    if (expectingValueForOpt) {
      result.push({ text: part, type: "value" });
      expectingValueForOpt = null;
      continue;
    }

    if (part.startsWith("-")) {
      const opt = activeSpec ? findOption(activeSpec, part) : null;
      if (opt && opt.takes_value) {
        expectingValueForOpt = opt;
        result.push({ text: part, type: "option" });
      } else {
        result.push({ text: part, type: "flag" });
      }
      continue;
    }

    result.push({ text: part, type: "value" });
  }

  return result;
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
