"""scripts/generate_shell_completion.py — emit static bash/zsh completion.

Reads ``cli_introspect/command_manifest.json`` and writes
``completions/investyo.bash`` and ``completions/investyo.zsh``. The generated
scripts are STATIC — they embed the command/option tables inline, so tab
completion is instant and never imports the heavy engines (unlike argcomplete,
which would import the target module on every keypress).

The completion hooks ``python`` / ``python3``: when the command line invokes a
known entry point (``python main.py``, ``python -m execution.kill_switch``,
``python scripts/preflight_check.py``, …) it offers that command's flags,
subcommands and — for zsh — their descriptions; otherwise it falls back to
ordinary file completion, so it never interferes with unrelated ``python`` runs.

    python scripts/generate_shell_completion.py
    source completions/investyo.zsh    # or .bash
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("generate_shell_completion")

MANIFEST_PATH = _REPO_ROOT / "cli_introspect" / "command_manifest.json"
COMPLETIONS_DIR = _REPO_ROOT / "completions"


# --------------------------------------------------------------------------- #
# Manifest → completion model
# --------------------------------------------------------------------------- #
@dataclass
class Context:
    ctx_id: str  # "main.py" | "prompt_registry" | "prompt_registry/get"
    kind: str  # "opts" | "subs"
    cands: List[str]  # completion candidates (option aliases, or subcommand names)
    disp: List[str]  # zsh display strings (candidate + " -- description")


def _cmd_key(cmd: Dict[str, Any]) -> str:
    """The token that identifies this command on the line.

    For scripts, the BASENAME (``"preflight_check.py"``) so it matches whether
    invoked as ``python scripts/preflight_check.py`` or from inside ``scripts/``
    as ``python preflight_check.py``; for ``-m`` targets, the dotted module
    (``"execution.kill_switch"``).
    """
    last = cmd["invocation"].split()[-1]
    return last if _is_module(cmd) else Path(last).name


def _is_module(cmd: Dict[str, Any]) -> bool:
    return " -m " in f" {cmd['invocation']} "


def _opt_aliases(spec: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(alias, description) for every option alias of a command/subcommand."""
    out: List[Tuple[str, str]] = []
    for opt in spec.get("options", []):
        desc = (opt.get("description") or "").strip()
        for alias in opt["aliases"]:
            out.append((alias, desc))
    return out


def _disp(alias: str, desc: str) -> str:
    return f"{alias}  -- {desc}" if desc else alias


def build_contexts(commands: List[Dict[str, Any]]) -> List[Context]:
    contexts: List[Context] = []
    for cmd in commands:
        key = _cmd_key(cmd)
        subs = cmd.get("subcommands") or []
        if subs:
            names = [sc["name"] for sc in subs]
            disp = [_disp(sc["name"], (sc.get("description") or "").strip()) for sc in subs]
            contexts.append(Context(key, "subs", names, disp))
            for sc in subs:
                items = _opt_aliases(sc)
                contexts.append(
                    Context(
                        f"{key}/{sc['name']}",
                        "opts",
                        [a for a, _ in items],
                        [_disp(a, d) for a, d in items],
                    )
                )
        else:
            items = _opt_aliases(cmd)
            contexts.append(
                Context(key, "opts", [a for a, _ in items], [_disp(a, d) for a, d in items])
            )
    return contexts


# --------------------------------------------------------------------------- #
# Shell literal helpers
# --------------------------------------------------------------------------- #
def _q(text: str) -> str:
    """Single-quote for bash/zsh (safe for descriptions with punctuation)."""
    return "'" + text.replace("'", "'\\''") + "'"


def _cmd_detect_arms(commands: List[Dict[str, Any]], indent: str) -> str:
    arms: List[str] = []
    for cmd in commands:
        key = _cmd_key(cmd)
        if _is_module(cmd):
            arms.append(f'{indent}{key}) [[ "$prevw" == "-m" ]] && cmd={_q(key)} ;;')
        else:
            arms.append(f"{indent}{key}|*/{key}) cmd={_q(key)} ;;")
    return "\n".join(arms)


def _sub_detect_blocks(commands: List[Dict[str, Any]], indent: str) -> str:
    blocks: List[str] = []
    for cmd in commands:
        subs = cmd.get("subcommands") or []
        if not subs:
            continue
        key = _cmd_key(cmd)
        arms: List[str] = []
        for sc in subs:
            tokens = "|".join([sc["name"], *sc.get("aliases", [])])
            arms.append(f'{indent}    {tokens}) sub={_q(sc["name"])} ;;')
        blocks.append(
            f'{indent}{key})\n'
            f'{indent}  if [[ -z "$sub" ]]; then\n'
            f'{indent}    case "$w" in\n'
            f"{chr(10).join(arms)}\n"
            f'{indent}    esac\n'
            f"{indent}  fi\n"
            f"{indent}  ;;"
        )
    return "\n".join(blocks)


def _ctx_arms(contexts: List[Context], indent: str, *, with_disp: bool) -> str:
    arms: List[str] = []
    for c in contexts:
        cands = " ".join(c.cands)
        lines = [f'{indent}{c.ctx_id}) kind={_q(c.kind)}; cands=({cands})']
        if with_disp:
            disp = " ".join(_q(d) for d in c.disp)
            lines.append(f"; disp=({disp})")
        arms.append("".join(lines) + " ;;")
    return "\n".join(arms)


# --------------------------------------------------------------------------- #
# bash
# --------------------------------------------------------------------------- #
def render_bash(commands: List[Dict[str, Any]], contexts: List[Context]) -> str:
    return f"""# investyo command completion (bash) — GENERATED by
# scripts/generate_shell_completion.py from cli_introspect/command_manifest.json.
# Do not edit by hand; re-run the generator. Enable with:  source completions/investyo.bash
_investyo_complete() {{
  local cur prevw w i cmd="" sub="" ctx kind="" cands
  cur="${{COMP_WORDS[COMP_CWORD]}}"
  for (( i=1; i < COMP_CWORD; i++ )); do
    w="${{COMP_WORDS[i]}}"
    prevw="${{COMP_WORDS[i-1]}}"
    if [[ -z "$cmd" ]]; then
      case "$w" in
{_cmd_detect_arms(commands, "        ")}
      esac
    else
      case "$cmd" in
{_sub_detect_blocks(commands, "        ")}
      esac
    fi
  done

  [[ -z "$cmd" ]] && {{ COMPREPLY=( $(compgen -f -- "$cur") ); return; }}

  ctx="$cmd"; [[ -n "$sub" ]] && ctx="$cmd/$sub"
  cands=()
  case "$ctx" in
{_ctx_arms(contexts, "    ", with_disp=False)}
  esac

  if [[ "$kind" == "opts" ]]; then
    if [[ "$cur" == -* ]]; then
      COMPREPLY=( $(compgen -W "${{cands[*]}}" -- "$cur") )
    else
      COMPREPLY=( $(compgen -f -- "$cur") )
    fi
  elif [[ "$kind" == "subs" ]]; then
    COMPREPLY=( $(compgen -W "${{cands[*]}}" -- "$cur") )
  else
    COMPREPLY=( $(compgen -f -- "$cur") )
  fi
}}
complete -F _investyo_complete python python3
"""


# --------------------------------------------------------------------------- #
# zsh
# --------------------------------------------------------------------------- #
def render_zsh(commands: List[Dict[str, Any]], contexts: List[Context]) -> str:
    return f"""#compdef python python3
# investyo command completion (zsh) — GENERATED by
# scripts/generate_shell_completion.py from cli_introspect/command_manifest.json.
# Do not edit by hand; re-run the generator. Enable with:  source completions/investyo.zsh
_investyo_complete() {{
  local cur w i cmd="" sub="" ctx kind=""
  local -a cands disp
  cur="${{words[CURRENT]}}"
  for (( i=2; i < CURRENT; i++ )); do
    w="${{words[i]}}"
    if [[ -z "$cmd" ]]; then
      local prevw="${{words[i-1]}}"
      case "$w" in
{_cmd_detect_arms(commands, "        ")}
      esac
    else
      case "$cmd" in
{_sub_detect_blocks(commands, "        ")}
      esac
    fi
  done

  if [[ -z "$cmd" ]]; then
    _files
    return
  fi

  ctx="$cmd"; [[ -n "$sub" ]] && ctx="$cmd/$sub"
  case "$ctx" in
{_ctx_arms(contexts, "    ", with_disp=True)}
  esac

  if [[ "$kind" == "opts" ]]; then
    if [[ "$cur" == -* ]]; then
      compadd -d disp -a cands
    else
      _files
    fi
  elif [[ "$kind" == "subs" ]]; then
    compadd -d disp -a cands
  else
    _files
  fi
}}
compdef _investyo_complete python python3
"""


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Generate bash/zsh completion from the command manifest.")
    parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="manifest path")
    parser.add_argument("--out-dir", default=str(COMPLETIONS_DIR), help="output directory")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        logger.error("manifest not found: %s — run scripts/build_command_manifest.py first", manifest_path)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    commands = manifest.get("commands", [])
    contexts = build_contexts(commands)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "investyo.bash").write_text(render_bash(commands, contexts), encoding="utf-8")
    (out_dir / "investyo.zsh").write_text(render_zsh(commands, contexts), encoding="utf-8")

    logger.info("wrote %s and %s (%d command(s))", out_dir / "investyo.bash", out_dir / "investyo.zsh", len(commands))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
