import { describe, expect, it } from "vitest";
import { parseCommandLine } from "./commandParse";
import type { CommandSpec } from "./api/types";

const opt = (name: string, extra: Partial<CommandSpec["options"][number]> = {}) => ({
  name,
  aliases: [name],
  description: null,
  default: null,
  choices: null,
  required: false,
  arg_kind: "optional" as const,
  metavar: null,
  takes_value: true,
  ...extra,
});

const COMMANDS: CommandSpec[] = [
  {
    name: "main.py",
    invocation: "python3 main.py",
    aliases: [],
    description: "advisory orchestrator",
    positionals: [],
    subcommands: [],
    options: [
      opt("--interval", { default: 0, metavar: "SECONDS" }),
      opt("--agent", { takes_value: false, arg_kind: "optional" }),
    ],
  },
  {
    name: "validation.harness",
    invocation: "python -m validation.harness",
    aliases: [],
    description: "validation harness",
    positionals: [],
    subcommands: [],
    options: [opt("--strategy", { required: true, arg_kind: "required" }), opt("--start", { default: "2020-01-01" })],
  },
  {
    name: "snapshot_diff.py",
    invocation: "python scripts/snapshot_diff.py",
    aliases: [],
    description: "diff snapshots",
    positionals: [],
    subcommands: [],
    options: [opt("--format", { choices: ["markdown", "json"], default: "markdown" })],
  },
  {
    name: "prompt_registry",
    invocation: "python -m prompt_registry",
    aliases: [],
    description: "prompt registry",
    positionals: [],
    options: [],
    subcommands: [
      {
        name: "get",
        invocation: "python -m prompt_registry get",
        aliases: ["g"],
        description: "fetch one",
        positionals: [
          { name: "id", description: "prompt id", default: null, choices: null, arg_kind: "required", metavar: null },
        ],
        subcommands: [],
        options: [opt("--version", { aliases: ["--version", "-v"] }), opt("--raw", { takes_value: false })],
      },
    ],
  },
];

describe("parseCommandLine", () => {
  it("suggests matching commands while typing the first token", () => {
    const r = parseCommandLine("harn", COMMANDS);
    expect(r.command).toBeNull();
    expect(r.suggestions.map((s) => s.value)).toContain("validation.harness");
  });

  it("empty input suggests every command", () => {
    expect(parseCommandLine("", COMMANDS).suggestions).toHaveLength(COMMANDS.length);
  });

  it("resolves a command and suggests its options after a space", () => {
    const r = parseCommandLine("main.py ", COMMANDS);
    expect(r.command?.name).toBe("main.py");
    expect(r.suggestions.map((s) => s.value)).toEqual(["--interval", "--agent"]);
  });

  it("filters option suggestions by the partial flag and hides used flags", () => {
    const r = parseCommandLine("main.py --interval 5 --ag", COMMANDS);
    expect(r.suggestions.map((s) => s.value)).toEqual(["--agent"]);
  });

  it("flags a missing required option before submit", () => {
    const r = parseCommandLine("validation.harness ", COMMANDS);
    expect(r.hints).toContainEqual({ level: "error", message: "missing required option: --strategy" });
  });

  it("clears the required hint once the option is supplied", () => {
    const r = parseCommandLine("validation.harness --strategy momentum", COMMANDS);
    expect(r.hints.find((h) => h.message.includes("--strategy"))).toBeUndefined();
    expect(r.composed).toBe("python -m validation.harness --strategy momentum");
  });

  it("flags an unknown option", () => {
    const r = parseCommandLine("main.py --bogus ", COMMANDS);
    expect(r.hints).toContainEqual({ level: "error", message: "unknown option: --bogus" });
  });

  it("completes an option's choices as values", () => {
    const r = parseCommandLine("snapshot_diff.py --format ", COMMANDS);
    expect(r.suggestions.map((s) => s.value)).toEqual(["markdown", "json"]);
    expect(r.suggestions[0].kind).toBe("value");
  });

  it("prompts for a subcommand and resolves it (incl. alias)", () => {
    const top = parseCommandLine("prompt_registry ", COMMANDS);
    expect(top.suggestions.map((s) => s.value)).toContain("get");
    expect(top.hints.some((h) => h.level === "warn")).toBe(true);

    const viaAlias = parseCommandLine("prompt_registry g ", COMMANDS);
    expect(viaAlias.subcommand?.name).toBe("get");
    // A required positional on the subcommand is flagged until supplied.
    expect(viaAlias.hints).toContainEqual({ level: "error", message: "missing required argument: id" });
  });

  it("composes the full invocation for a resolved subcommand", () => {
    const r = parseCommandLine("prompt_registry get my.prompt --raw", COMMANDS);
    expect(r.subcommand?.name).toBe("get");
    expect(r.composed).toBe("python -m prompt_registry get my.prompt --raw");
    expect(r.hints).toHaveLength(0);
  });

  it("flags an unknown command", () => {
    const r = parseCommandLine("nope ", COMMANDS);
    expect(r.hints).toContainEqual({ level: "error", message: "unknown command: nope" });
  });
});
