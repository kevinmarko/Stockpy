---
name: mcp-widget-builder
description: Guide for building and bundling new Ext-Apps widgets for the MCP server. Use when adding new interactive UI elements to the web app.
---

# MCP Widget Builder Skill

This skill outlines the process for building and bundling vanilla HTML/JS widgets for the Stockpy MCP server.

## 1. Widget Architecture

Widgets in this platform are built using Vanilla HTML/JS and Chart.js. We do NOT use React or other heavy frameworks to keep the bundle size small and maintain parity with existing widgets. 

Widgets expect a JSON payload returned by an MCP tool and render it dynamically inside the `ext-apps` boundary.

## 2. Steps to Add a New Widget

1. **Create the Template**: Create your HTML file (e.g., `my-widget.html`) inside `mcp_widgets/templates/`. Use `/*__EXT_APPS_BUNDLE__*/` and `/*__WIDGET_COMMON_JS__*/` placeholders for CSS/JS imports.
2. **Register the Resource**: Edit `mcp_widget_resources.py` and add your widget to the `_WIDGET_RESOURCES` list, mapping it to a `ui://widgets/...` URI.
3. **Build the Bundle**:
```bash
cd mcp_widgets/build
npm run build
```
This command runs `build_bundle.mjs` and bundles Chart.js with the `ext-apps-bundle.js`.

## 3. Common Failure Modes & Fixes

**Failure Mode: Widget renders as empty or doesn't update**
- **Symptom:** The MCP client requests the UI resource, but it shows up blank or doesn't update when the tool returns data.
- **Fix:** 
  1. Check if the tool actually returned a JSON block (wrapped in ```json ... ``` markdown). The widget's `extractJsonPayload(content[0].text)` relies on this.
  2. Verify that you ran `npm run build` inside `mcp_widgets/build`. `ext-apps-bundle.js` must contain the compiled code.

**Failure Mode: `ui://` Resource Not Found**
- **Symptom:** The client attempts to open `ui://widgets/my-widget.html` but receives a 404/not found from FastMCP.
- **Fix:** You forgot to register the URI in `mcp_widget_resources.py` inside `_WIDGET_RESOURCES`.
