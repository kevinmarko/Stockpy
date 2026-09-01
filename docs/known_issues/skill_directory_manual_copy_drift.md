# `.agents/skills/` vs `.claude/skills/` manual-copy drift

**Status: fixed and verified, plus a new automated regression test and one
more real, previously-unfound divergence closed in the same follow-up pass.**

## What happened

PR #970 ("Add stockpy-master-prompt skill and quant integrity parity skill")
added a new `.agents/skills/stockpy-master-prompt/SKILL.md` (mirrored, per
its own implementation plan, into `.claude/skills/stockpy-master-prompt/
SKILL.md`) and a new `.agents/skills/stockpy-quant-integrity/SKILL.md`,
explicitly described in the plan as "Mirrored from `.claude/skills/
stockpy-quant-integrity/SKILL.md` to ensure Antigravity has local access to
the quant integrity reference cited in the startup ritual."

Neither mirror was actually verified against its counterpart before merge —
the PR's own "Verification" section ran two tests unrelated to the changed
files and "Verified skill frontmatter schema and markdown rendering," which
checks that the files parse, not that they say the same, correct thing. Two
real divergences shipped as a result: `.agents/skills/stockpy-quant-
integrity/SKILL.md` reproduced an already-superseded claim that
`earnings_crush`/`dispersion_trading`/`zero_dte_engine`/`gamma_scalper`/
`copula_stat_arb` "have zero `STRATEGY_REGISTRY` entries" (all six are
registered), and the new `stockpy-master-prompt` skill's own §7 "Known open
gaps" contradicted that sibling file, added in the SAME commit, by claiming
`zero_dte_engine`'s 15:45 ET exit gate is "never called from any production
path" (it is, wired into `desktop/daemon_runtime.py`'s `_timer_loop`).

**PR #972** (`c274c34a`, "Audit+fix pass on PR #970") fixed both of these —
independently, concurrently with this write-up — re-syncing the two
`stockpy-quant-integrity` copies and rewriting `stockpy-master-prompt`'s §7
to stop restating specific, volatile facts inline and instead point at
`stockpy-quant-integrity`'s own maintained "Currently open, already-flagged
gaps" section. See that commit's message for its own full account, including
a third finding (an unverifiable "~28 modules" claim in §1) this write-up
doesn't repeat.

## What this follow-up pass adds on top of PR #972

Two things PR #972 did not have: a regression test, and a broader sweep for
the same bug class elsewhere in the two skill trees.

### 1. New regression test

`tests/test_skill_directory_parity.py` asserts `stockpy-master-prompt` and
`stockpy-quant-integrity` — the only two skills confirmed to be intended as
true byte-identical mirrors — stay in sync across `.agents/skills/` and
`.claude/skills/`. Verified both directions: passes against the current
(PR #972-fixed) state, and fails with a first-differing-line diff on a
scratch mutation of one copy (reverted immediately after).

### 2. A second, real, previously-unfound divergence: `agentic-discovery`

Sweeping every skill present in both trees found 8 already differing by a
consistent ~8-line HTML-comment preamble in the `.agents/` copy (e.g.
"Ported from this repo's Claude Code sibling skill... to Antigravity's
skill format... no restructuring was required for this port beyond this
note.") — `jules-delegation`, `mcp-widget-builder`, `new-pwa-screen`,
`new-signal-module`, `pilots-endpoint`, `robinhood-execution`,
`strategy-validation`, and `agentic-discovery`. Stripping that preamble and
diffing the remaining body confirmed 7 of the 8 are genuinely
body-identical — the preamble really is just a porting note, not a sign of
drift. **`agentic-discovery` was the exception**, and the drift was real
content, not cosmetic:

- `.agents/skills/agentic-discovery/SKILL.md` (before this fix) told an
  Antigravity agent that `main._load_watchlist()` gives the `WATCHLIST` env
  var precedence over `watchlist.txt` — "if it's set, appending to
  `watchlist.txt` would be silently ineffective" — and instructed it to ask
  the operator whether to append to `WATCHLIST` instead or clear it first.
- `.claude/skills/agentic-discovery/SKILL.md` correctly said
  `main._load_watchlist()` **unions both sources, deduped, with neither
  taking precedence** — quoting the function's own docstring verbatim.

Confirmed against `main.py:267-286` (`_load_watchlist()`'s docstring: "Both
sources are read (when present) and merged/deduped -- neither one takes
precedence over the other"). The `.agents/` copy was simply wrong: an
Antigravity agent following it would have interrupted the operator with a
false precedence conflict, or worse, told them an append to `watchlist.txt`
was "silently ineffective" when it never was. Fixed by replacing the wrong
`.agents/` paragraph with the correct `.claude/` text (the porting-note
preamble is otherwise untouched, since it's a legitimate, deliberate
per-platform note, not the bug). Re-verified: the two copies are now
body-identical beyond that preamble, same as the other 7.

This divergence was not something PR #972 looked for — its scope was
`stockpy-master-prompt`/`stockpy-quant-integrity` specifically, following
the operator's original request. It's disclosed here as a reminder that a
"parity" bug found in one skill pair is a signal to check the wider pattern,
not just the two files originally reported.

## What's still not automated

`EXACT_MIRROR_SKILLS` in `tests/test_skill_directory_parity.py`
deliberately covers only `stockpy-master-prompt`/`stockpy-quant-integrity` —
the two skills confirmed to have no porting-note preamble and to be meant as
literal byte-identical mirrors. The 8 ported skills (now including the
`agentic-discovery` fix above) are correct as of this write-up but have no
automated check of their own: nothing prevents a future edit to one copy's
body from silently drifting from the other again, since a byte-identity
assertion would fail on the legitimate preamble difference. A real follow-up
would need a preamble-aware diff (strip the known HTML-comment block, then
compare) generalized into the test, rather than hand-verifying all 8 again
next time.
