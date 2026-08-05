---
description: Launch the Pilots PWA locally and use the browser to confirm a change actually renders and works, not just typechecks
---

A clean `tsc --noEmit` (already enforced automatically by the
`webapp_typecheck.sh` PostToolUse hook on every edit under `webapp/src/**`)
only proves the code *compiles* — it proves nothing about whether the
screen actually renders, whether the console is clean, or whether the
change behaves the way it was intended to. This command closes that gap by
actually driving the running app in a browser.

1. Confirm `webapp/package.json` exists. If a dev server isn't already
   running, start one in the background:
   ```
   npm run dev --prefix webapp
   ```
   This serves against the offline mock API layer by default
   (`VITE_USE_MOCK` defaults true) — only set `VITE_USE_MOCK=false` if the
   operator explicitly asked to check against the live backend.

2. Poll until `http://localhost:5173` responds.

3. Use the browser tool to open the affected screen/route, read the
   console messages for errors, and read/screenshot the page to visually
   confirm the change actually looks and behaves as intended — not just
   that something rendered.

4. Report any console errors verbatim. A red console error is a fail even
   if the page otherwise rendered something plausible-looking.

5. If the operator is still iterating, leave the dev server running for
   the next round; otherwise stop it.

$ARGUMENTS
