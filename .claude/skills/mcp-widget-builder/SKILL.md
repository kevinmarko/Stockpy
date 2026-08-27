---
name: mcp-widget-builder
description: >-
  Add or modify an MCP Apps SDK widget (interactive HTML rendered inline by
  an MCP host) served by investyo_mcp_server.py via mcp_widget_resources.py.
  Use when asked to add a new widget template, wire a widget to an MCP tool,
  fix a widget rendering blank/stale, or rebuild the vendored ext-apps
  bundle -- covers the real placeholder-substitution pipeline
  (mcp_widgets/build/build_bundle.mjs + mcp_widget_resources.py), the
  template/register/build/wire procedure, and the graceful-degradation
  contract when the one-time npm build hasn't been run.
---

# Building an MCP widget (Apps SDK)

**Note on staleness**: widget templates are actively being built out in this
repo (multiple `mcp_widgets/templates/*.html` files exist that are not yet
wired to any tool — see §5). This skill documents the *procedure*, verified
against the real `mcp_widget_resources.py`/`build_bundle.mjs`/an existing
wired template (`pilot-detail.html`'s pattern, `_common.js`) as of this
writing — not a frozen list of "the" widgets. Re-verify file contents before
relying on exact line numbers if this has since been refactored.

## Architecture, as it actually works

Two separate build stages, easy to conflate — know which one you need:

1. **`mcp_widgets/build/build_bundle.mjs`** (run via `npm run build` inside
   `mcp_widgets/build/`, i.e. `node build_bundle.mjs`) is a **one-time
   vendoring step**, not a per-widget build. It does exactly one thing:
   concatenates `chart.js` (`node_modules/chart.js/dist/chart.umd.js`) with a
   rewritten copy of `@modelcontextprotocol/ext-apps`'s `app-with-deps`
   bundle (its `export{...}` statement is regex-rewritten into
   `globalThis.ExtApps={...}` so a plain `<script>` tag can consume it
   without a module loader) and writes the result to
   `mcp_widgets/vendor/ext-apps-bundle.js`. You only need to re-run this when
   you change the `chart.js`/`@modelcontextprotocol/ext-apps` dependency
   versions in `mcp_widgets/build/package.json` — **not** every time you add
   or edit a widget template.
2. **`mcp_widget_resources.py::render_widget_html()`** is the actual
   per-widget template substitution — it runs in Python, at MCP server
   startup, not in the JS build. For a given template filename it reads
   `mcp_widgets/vendor/ext-apps-bundle.js` (from step 1) + the two shared
   files `mcp_widgets/templates/_common.css` / `_common.js` + your template,
   and does three literal string replacements:

   | Placeholder (exact token, inside a comment) | Replaced with |
   |---|---|
   | `/*__EXT_APPS_BUNDLE__*/` | the vendored bundle from step 1 |
   | `/*__WIDGET_COMMON_CSS__*/` | `mcp_widgets/templates/_common.css` |
   | `/*__WIDGET_COMMON_JS__*/` | `mcp_widgets/templates/_common.js` |

   Returns `None` (never raises) if the vendored bundle or either common
   file is missing — this is the graceful-degradation path (§4).

## Procedure: adding a new widget

1. **Read an existing, real (non-stub) template first** — e.g.
   `mcp_widgets/templates/equity-curve.html`, a small ~80-line example that
   shows the required shape: a bare `<!doctype html>` (no `<html>`/`<head>`
   wrapper), a `<style>` block containing the `/*__WIDGET_COMMON_CSS__*/`
   placeholder plus any widget-specific CSS, a `<body>` with your markup,
   and a `<script type="module">` block containing
   `/*__EXT_APPS_BUNDLE__*/` then `/*__WIDGET_COMMON_JS__*/` (in that order
   — the common JS helpers assume `globalThis.ExtApps` already exists) then
   your widget's own script. The Apps SDK object is `const { App } =
   globalThis.ExtApps;` — construct one `App`, call `app.ontoolresult =
   ({content}) => {...}` to react to tool output, `await app.connect()`, and
   read `app.getHostContext()?.theme` (call `applyHostTheme(theme)` from
   `_common.js` — it toggles a `light` class on `<html>`; also register
   `app.onhostcontextchanged`).
2. **Use `_common.js`'s helpers rather than re-implementing them.** As of
   this writing it exports `extractJsonPayload(text)` (pulls the fenced
   ```` ```json ... ``` ```` block a tool's text output embeds — every widget
   parses its payload this way, since MCP tool results are markdown+JSON,
   not structured JSON alone), `formatCurrency`, `fmtMetric`, `fmtPct`,
   `deployableBadge` (renders the `✅ Deployable`/`❌ Not Deployable`/`—
   Unrated` badge every strategy-metric widget uses), `categoryChip`,
   `applyHostTheme`, plus larger composite renderers
   (`renderDetailPanel`, `renderComparePanel`, `renderFollowResultCard`,
   `renderPortfolioByPilotPanel`) built for specific existing widgets — check
   whether your widget's shape already has a composite renderer here before
   writing DOM-construction code from scratch.
3. **Create the template** at `mcp_widgets/templates/<name>.html`.
4. **Register it** in `mcp_widget_resources.py`'s `_WIDGET_RESOURCES` list —
   a list of `(template_filename, "ui://widgets/<name>.html", "Human Title")`
   tuples. This is what makes `register_widget_resources(mcp)` register the
   rendered HTML as a FastMCP resource at server startup — a template that
   exists on disk but isn't in this list is never served.
5. **Wire it to a tool** in `investyo_mcp_server.py`. The pattern (see the
   existing `_PILOT_PICKER_UI`/`_PILOT_DETAIL_UI`/`_FOLLOW_RESULT_UI`/
   `_PILOT_COMPARE_UI`/`_PILOT_PORTFOLIO_UI` module-level constants near the
   top of the file):
   ```python
   _WIDGETS_AVAILABLE = mcp_widget_resources.register_widget_resources(mcp)
   _MY_WIDGET_UI = (
       {"ui": {"resourceUri": "ui://widgets/my-widget.html"}}
       if _WIDGETS_AVAILABLE else None
   )

   @mcp.tool(meta=_MY_WIDGET_UI, annotations=ToolAnnotations(readOnlyHint=True))
   def my_tool(...) -> str:
       ...
   ```
   The `if _WIDGETS_AVAILABLE else None` guard is load-bearing — if the
   vendored bundle was never built, `meta` must be `None` so the tool still
   works as plain markdown/JSON text; never hardcode a `resourceUri` that
   might not exist as a registered resource. The tool's own return value is
   unaffected by whether `meta` is set — the widget consumes the SAME text
   output via `extractJsonPayload`, so changing/adding `meta` never changes
   what the tool returns to a non-widget-capable client.
6. **Build the vendor bundle if you haven't already** (one-time per machine,
   or after a dependency bump):
   ```bash
   cd mcp_widgets/build && npm install && npm run build
   ```
   This writes `mcp_widgets/vendor/ext-apps-bundle.js`. Restart the MCP
   server after this — `register_widget_resources()` only runs at import
   time.
7. **Add a test — both halves, not just one.** This repo has two genuinely
   different test surfaces for a widget; a change usually needs both:
   - **Payload/wiring (Python)**: follow `tests/test_investyo_mcp_widgets.py`'s
     pattern. `TestRenderWidgetHtml`-style tests build fixtures entirely
     inside `tmp_path` via `monkeypatch.setattr` on
     `mcp_widget_resources.BUNDLE_PATH`/`TEMPLATES_DIR` (so they don't depend
     on the real npm build having run in CI), while
     `TestToolMetaWiringConsistency`-style tests assert against the real
     `investyo_mcp_server.py` module-level `_WIDGETS_AVAILABLE`/
     `_MY_WIDGET_UI` constants and that adding `meta` didn't change the
     tool's text output. This proves the *shape* of what a tool hands the
     widget is right — it does not execute a single line of widget JS.
   - **Rendering (JS, real DOM — `mcp_widgets/tests/`)**: `node --check
     mcp_widgets/templates/_common.js` only proves the file parses; it
     proves nothing about what a render function actually puts in the DOM.
     `mcp_widgets/tests/render.test.mjs` (Node's built-in test runner +
     `jsdom`, that directory's one devDependency) loads `_common.js` via
     `vm.runInContext` against a real jsdom window — the same plain
     global-function script `mcp_widget_resources.py`'s placeholder
     substitution serves to a real MCP host, not a rewritten copy — and
     calls each render function with real and edge-case payloads, asserting
     on the actual rendered DOM (text content, CSS classes, element counts).
     This is what would have caught (and, once written, DID catch on the
     first real run) failure modes payload-shape tests alone cannot: a
     fabricated-looking fallback value surviving in the DOM instead of
     `"—"`, a botched cherry-pick leaving inconsistent indentation, or a
     debounced/async handler (like `renderStrategyTuner`'s live-recompute)
     firing when it shouldn't. Run: `cd mcp_widgets/tests && npm install &&
     npm test`. Add a new `describe()` block per render function you touch;
     follow the existing file's per-test `freshCommonJs()` isolation
     pattern (a fresh jsdom window per test — functions with debounce/timer
     state must never leak between tests) rather than sharing one `window`
     across a whole file.

## Widget payload contract

A widget never receives structured data directly from the MCP transport in
this codebase's implementation — it receives the SAME markdown+text a
non-widget client would see, and extracts the JSON via
`extractJsonPayload()`'s fenced-code-block regex
(`` /```json\s*\n([\s\S]*?)\n```\s*$/ ``, anchored to the END of the text).
This means: **every tool that wants a widget must end its returned string
with a fenced ` ```json ` block containing the exact payload the widget
expects** — check the existing tool's return-string construction (e.g. the
`get_pilot_detail`/`compare_pilots`/`get_portfolio_by_pilot` tools near the
`_PILOT_DETAIL_UI`/`_PILOT_COMPARE_UI`/`_PILOT_PORTFOLIO_UI` wiring) for the
exact shape before writing new widget-side rendering code against it.

## 4. Graceful degradation (never break the non-widget path)

`register_widget_resources()` registers **either all** listed widgets **or
none** — if any template/common file/bundle is missing for any entry in
`_WIDGET_RESOURCES`, it logs one actionable warning (naming the fix:
`cd mcp_widgets/build && npm install && npm run build`) and returns `False`,
which is what makes every `_XXX_UI` constant resolve to `None` and every
`meta=` argument become a no-op. This is deliberate — a host must never be
pointed at a `ui://` resource that doesn't actually exist. Every MCP tool in
this server must keep working as plain text with widgets disabled; this is
cosmetic, additive functionality, never load-bearing for the platform (see
`mcp_widget_resources.py`'s own module docstring).

## Common failure modes & fixes

**Widget resource registered but renders blank / never updates.**
1. Confirm the tool's return text actually ends with a fenced ` ```json `
   block — `extractJsonPayload`'s regex is anchored to the end of the string
   (`\s*$`), so trailing prose after the closing ` ``` ` breaks the match
   silently (returns `null`, and `ontoolresult` bails on a falsy payload).
2. Confirm `npm run build` was actually run from `mcp_widgets/build/` after
   the LAST dependency change — `ext-apps-bundle.js` is gitignored/vendored
   output, not checked in verbatim on every commit; a stale bundle can be
   missing exports a newer `_common.js` helper expects.

**`ui://widgets/<name>.html` resource not found / 404 from the MCP host.**
You added the template file but forgot to add its tuple to
`_WIDGET_RESOURCES` in `mcp_widget_resources.py` — the registration list is
the only thing that makes a template file actually servable; a file sitting
in `mcp_widgets/templates/` alone is inert.

**Server logs the "MCP widget assets not built" warning even though you
just ran `npm run build`.** Check you ran it from `mcp_widgets/build/` (the
script writes to `../vendor/ext-apps-bundle.js`, a path relative to that
directory) and that the MCP server process was actually restarted afterward
— `register_widget_resources()` runs once at import time, so a long-running
server process won't pick up a bundle that appeared after it started.
