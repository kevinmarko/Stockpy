// Real DOM tests for mcp_widgets/templates/_common.js's render functions.
//
// Closes the "no JS test runner exists in this repo" gap disclosed in
// .claude/mcp_widget_contracts_and_browser_diagnostics_walkthrough.md --
// prior to this file, `node --check` proved syntax only and the Python
// tests in tests/test_investyo_mcp_widgets.py proved payload-shape
// correctness only. Nothing executed these render functions against a real
// DOM and asserted on the rendered output. This file does.
//
// Uses Node's built-in test runner (node --test, zero extra runner
// dependency) + jsdom for a real `document`/`window` (this package's only
// devDependency). _common.js is loaded via `vm.runInContext` against a
// jsdom window's context -- it's a plain global-function script (no ES
// module exports, no bundler), exactly how mcp_widget_resources.py's real
// placeholder-substitution pipeline serves it to an MCP host, so loading it
// this way exercises the actual shipped code, not a rewritten copy.
//
// Run: cd mcp_widgets/tests && npm install && npm test

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";
import { JSDOM } from "jsdom";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const COMMON_JS_PATH = path.resolve(__dirname, "../templates/_common.js");
const COMMON_JS_SOURCE = readFileSync(COMMON_JS_PATH, "utf8");

/**
 * Fresh jsdom window + a fresh vm context with _common.js's globals loaded
 * into it, per test -- functions like renderStrategyTuner close over
 * module-level-looking `let debounceTimer`/`requestSeq` state per call, but
 * a shared context across tests would still leak `document` mutations
 * (host theme class, etc.) between unrelated tests. Isolate every test.
 */
function freshCommonJs() {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    url: "https://widget.invalid/",
    pretendToBeVisual: true, // gives us a working setTimeout/clearTimeout on window
    runScripts: "dangerously", // required for getInternalVMContext(); this is a static, trusted, repo-local script, not remote/untrusted content
  });
  const context = dom.getInternalVMContext
    ? dom.getInternalVMContext()
    : dom.window; // jsdom >=16 exposes getInternalVMContext; fall back to window for older versions
  vm.runInContext(COMMON_JS_SOURCE, context, { filename: "_common.js" });
  return { dom, window: dom.window, document: dom.window.document };
}

describe("renderPitMatrix", () => {
  test("renders real payload keys (pit_rows/earliest_report_date/latest_report_date)", () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    const payload = {
      rows: [
        { symbol: "AAPL", pit_rows: 42, earliest_report_date: "2015-01-15", latest_report_date: "2026-07-01" },
      ],
    };
    window.renderPitMatrix(container, payload);
    const text = container.textContent;
    assert.match(text, /AAPL/);
    assert.match(text, /42/);
    assert.match(text, /2015-01-15/);
    assert.match(text, /2026-07-01/);
    // Regression guard: a fully-populated row must render with zero "—"
    // cells -- if the JS silently fell back to the OLD wrong key names
    // (r.rows/r.earliest/r.latest), every one of these would read "—"
    // instead of the real values just asserted above.
    const cells = [...container.querySelectorAll("td")].map((td) => td.textContent.trim());
    assert.ok(cells.slice(0, 4).every((c) => c !== "—"), `expected no dash cells, got: ${JSON.stringify(cells)}`);
  });

  test("empty rows array renders the honest empty state, not a fabricated row", () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    window.renderPitMatrix(container, { rows: [] });
    assert.match(container.textContent, /No PIT coverage rows available\./);
    assert.equal(container.querySelectorAll("table").length, 0);
  });

  test("a row missing pit_rows/dates renders —, never a fabricated 0 or blank", () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    window.renderPitMatrix(container, { rows: [{ symbol: "ZZZZ" }] });
    const cells = [...container.querySelectorAll("td")].map((td) => td.textContent.trim());
    // Symbol, Rows, Earliest, Latest, Lag-buffer cells in that order.
    assert.equal(cells[1], "—");
    assert.equal(cells[2], "—");
    assert.equal(cells[3], "—");
  });
});

describe("renderModelDiagnostics", () => {
  test("renders the real payload shape: top-level horizon_days, per-row pending/completed/skill_weights", () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    const payload = {
      horizon_days: 5,
      rows: [
        { symbol: "MSFT", pending: 3, completed: 12, skill_weights: { lgbm: 0.62, prophet: 0.38 } },
      ],
    };
    window.renderModelDiagnostics(container, payload);
    const text = container.textContent;
    assert.match(text, /Horizon: 5d/);
    assert.match(text, /MSFT/);
    assert.match(text, /3/); // pending
    assert.match(text, /12/); // completed
    assert.match(text, /lgbm: 0\.62/);
    assert.match(text, /prophet: 0\.38/);
    // Regression guard against the fields the real payload never has --
    // if these leak in as literal text, a stale/wrong key read regressed.
    assert.doesNotMatch(text, /drift_detected/);
    assert.doesNotMatch(text, /decay_pct/);
  });

  test("empty rows shows the real `reason` field when the tool provided one", () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    window.renderModelDiagnostics(container, { horizon_days: 30, rows: [], reason: "No symbols have completed forecasts yet." });
    assert.match(container.textContent, /No symbols have completed forecasts yet\./);
  });

  test("a row with no skill_weights renders —, not an empty string or fabricated weight", () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    window.renderModelDiagnostics(container, { rows: [{ symbol: "GLD", pending: 0, completed: 0 }] });
    const rowText = container.querySelector("tbody tr").textContent;
    assert.match(rowText, /—/);
  });

  test("consistent 4-space indentation (regression guard for the botched-cherry-pick failure mode)", () => {
    // mcp_widgets/templates/_common.js:1168-1219 -- this function was
    // independently reindented (2-space -> 4-space) by an unrelated commit
    // after the payload-contract fix landed on a stale base; a careless
    // patch/cherry-pick previously reintroduced a stray 2-space line here.
    // node --check only proves the file parses, not that indentation is
    // consistent -- assert on it directly so a regression fails loudly.
    const lines = COMMON_JS_SOURCE.split("\n");
    const start = lines.findIndex((l) => l.startsWith("function renderModelDiagnostics"));
    const end = lines.findIndex((l, i) => i > start && l === "}");
    assert.ok(start > -1 && end > start, "could not locate renderModelDiagnostics function bounds");
    const body = lines.slice(start + 1, end).filter((l) => l.trim().length > 0);
    for (const line of body) {
      const indent = line.match(/^ */)[0].length;
      assert.equal(indent % 4, 0, `expected 4-space-multiple indentation, got ${indent} spaces on: ${JSON.stringify(line)}`);
    }
  });
});

describe("renderLighthouseScorecard", () => {
  test("real scores/vitals/vitals_rating render actual measured values and ratings", () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    const payload = {
      scores: { performance: 94, accessibility: null, bestPractices: null, seo: null },
      vitals: { ttfb_ms: 120, fcp_ms: 900, lcp_ms: 1800, cls: 0.05 },
      vitals_rating: { ttfb_ms: "good", fcp_ms: "good", lcp_ms: "good", cls: "good" },
    };
    window.renderLighthouseScorecard(container, payload);
    const text = container.textContent;
    assert.match(text, /94/);
    assert.match(text, /120/);
    assert.equal(container.querySelectorAll(".score-good").length, 1);
    assert.equal(container.querySelectorAll(".score-unmeasured").length, 3);
    assert.equal(container.querySelectorAll(".vital-rating.good").length, 4);
  });

  test("unmeasured scores/vitals render — and a neutral class, NEVER the old fabricated defaults (90/95/100/90, 0.8s/0.01/0.6s/95ms)", () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    window.renderLighthouseScorecard(container, {});
    const text = container.textContent;
    // The 4 score gauges must all read the unmeasured dash, never a number.
    assert.equal(container.querySelectorAll(".gauge-circle").length, 4);
    for (const circle of container.querySelectorAll(".gauge-circle")) {
      assert.equal(circle.textContent, "—");
      assert.match(circle.className, /score-unmeasured/);
    }
    // No vitals were provided -> zero vital cards rendered (the function
    // skips a vital entirely on a null value rather than fabricating one).
    assert.equal(container.querySelectorAll(".vital-card").length, 0);
    assert.doesNotMatch(text, /\b90\b/);
    assert.doesNotMatch(text, /0\.8s/);
    assert.doesNotMatch(text, /95ms/);
  });

  test("a vital present but with no rating shows ● Unrated, not a fabricated ● Good", () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    window.renderLighthouseScorecard(container, { vitals: { ttfb_ms: 250 } });
    const rating = container.querySelector(".vital-rating");
    assert.equal(rating.textContent, "● Unrated");
    assert.doesNotMatch(rating.className, /\bgood\b/);
  });
});

describe("renderBacktestTearSheet", () => {
  test("real max_drawdown/total_return render the correct percentage, not double-formatted garbage or a permanent —", () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    window.renderBacktestTearSheet(container, { symbol: "SPY", sharpe: 1.5, dsr: 0.97, pbo: 0.1, max_drawdown: -0.184, total_return: 0.42 });
    const values = [...container.querySelectorAll(".stat-value")].map((el) => el.textContent);
    assert.equal(values[3], "-18.4%"); // Max DD
    assert.equal(values[4], "42.0%"); // Total Return
    // fmtMetric-formatted fields still work correctly alongside the fix.
    assert.equal(values[0], "1.50"); // Sharpe
  });

  test("null max_drawdown/total_return render —, the honest CONSTRAINT #4 degrade -- this is the exact bug the audit found and fixed (round(None) crash upstream, double-fmtMetric here)", () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    window.renderBacktestTearSheet(container, { symbol: "SPY", sharpe: null, dsr: null, pbo: null, max_drawdown: null, total_return: null });
    const values = [...container.querySelectorAll(".stat-value")].map((el) => el.textContent);
    assert.deepEqual(values, ["—", "—", "—", "—", "—"]);
  });
});

describe("renderMacroRegimeRadar", () => {
  test("kill_switch_active: true, false, and null/undefined render three DISTINCT badges", () => {
    for (const [value, expectedText, expectedClass] of [
      [true, "Kill Switch Active", "badge-decline"],
      [false, "Normal Operation", "badge-growth"],
      [null, "Kill Switch Unknown", "badge-caution"],
      [undefined, "Kill Switch Unknown", "badge-caution"],
    ]) {
      const { window, document } = freshCommonJs();
      const container = document.createElement("div");
      window.renderMacroRegimeRadar(container, { market_regime: "NEUTRAL", kill_switch_active: value });
      const badge = container.querySelectorAll(".badge")[0];
      assert.equal(badge.textContent, expectedText, `kill_switch_active=${value}`);
      assert.match(badge.className, new RegExp(expectedClass), `kill_switch_active=${value}`);
    }
  });
});

describe("renderVisualDiff", () => {
  test("baseline_established, match, and no-match render three distinct badges", () => {
    const cases = [
      [{ baseline_established: true }, "🆕 Baseline Established"],
      [{ baseline_established: false, match: true }, "100% Match"],
      [{ baseline_established: false, match: false }, "Visual Diff Detected"],
    ];
    for (const [payload, expected] of cases) {
      const { window, document } = freshCommonJs();
      const container = document.createElement("div");
      window.renderVisualDiff(container, payload);
      assert.match(container.textContent, new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }
  });
});

describe("renderStrategyTuner", () => {
  test("liveCapable=true: dragging a slider debounces then calls app.callServerTool with the updated state, and re-renders from the REAL response", async () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    const calls = [];
    const app = {
      callServerTool: async (req) => {
        calls.push(req);
        return {
          content: [{
            text: '```json\n' + JSON.stringify({ simulated_sharpe: 1.75, simulated_max_dd_pct: 9.1, simulated_win_rate_pct: 61.2 }) + '\n```',
          }],
        };
      },
    };
    window.renderStrategyTuner(container, { strategy_name: "rsi2_mean_reversion", rsi_lower: 25 }, app);

    const slider = container.querySelector('input[type="range"]');
    assert.ok(slider, "expected a slider input to be rendered");
    slider.value = "18";
    slider.dispatchEvent(new window.Event("input"));

    assert.match(container.querySelector(".tuner-status-line").textContent, /Recalculating/);

    // Debounce is 350ms; wait past it for the real (mocked) tool call + re-render.
    await new Promise((resolve) => window.setTimeout(resolve, 500));

    assert.equal(calls.length, 1);
    assert.equal(calls[0].name, "tune_strategy_parameters");
    assert.equal(calls[0].arguments.rsi_lower, 18);

    const statValues = [...container.querySelectorAll(".stat-value")].map((el) => el.textContent);
    assert.equal(statValues[0], "1.75");
    assert.equal(statValues[1], "9.1%");
    assert.equal(statValues[2], "61.2%");
    assert.equal(container.querySelector(".tuner-status-line").textContent, "");
  });

  test("a fast second slider drag supersedes a slower first response (race guard) -- the stale response never overwrites the newer one", async () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    let resolveFirst;
    const firstCallPromise = new Promise((r) => { resolveFirst = r; });
    let callCount = 0;
    const app = {
      callServerTool: async (req) => {
        callCount += 1;
        if (callCount === 1) {
          await firstCallPromise; // hang the first call until we release it below
          return { content: [{ text: '```json\n' + JSON.stringify({ simulated_sharpe: 1.0, simulated_max_dd_pct: 20.0, simulated_win_rate_pct: 40.0 }) + '\n```' }] };
        }
        return { content: [{ text: '```json\n' + JSON.stringify({ simulated_sharpe: 2.0, simulated_max_dd_pct: 5.0, simulated_win_rate_pct: 70.0 }) + '\n```' }] };
      },
    };
    window.renderStrategyTuner(container, { strategy_name: "rsi2_mean_reversion" }, app);
    const slider = container.querySelector('input[type="range"]');

    slider.value = "15";
    slider.dispatchEvent(new window.Event("input"));
    await new Promise((resolve) => window.setTimeout(resolve, 400)); // let the first debounce fire and the call start (and hang)

    slider.value = "12";
    slider.dispatchEvent(new window.Event("input"));
    await new Promise((resolve) => window.setTimeout(resolve, 400)); // let the second debounce fire, call, and resolve

    // Now release the first (slow, stale) call.
    resolveFirst();
    await new Promise((resolve) => window.setTimeout(resolve, 50));

    const statValues = [...container.querySelectorAll(".stat-value")].map((el) => el.textContent);
    // Must reflect the SECOND (newer) response (2.0/5.0%/70.0%), never the
    // first (stale) one that resolved last (1.0/20.0%/40.0%).
    assert.equal(statValues[0], "2.00");
    assert.equal(statValues[1], "5.0%");
    assert.equal(statValues[2], "70.0%");
  });

  test("liveCapable=false (host has no app.callServerTool): renders a static status message, and dragging a slider NEVER fires a recompute -- regression test for the exact bug the audit found (scheduleRecompute() firing anyway, only caught by try/catch, contradicting the \"static\" message with a spurious \"Recalculating…\" flash)", async () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    window.renderStrategyTuner(container, { strategy_name: "rsi2_mean_reversion" }, {}); // app with no callServerTool
    const statusLine = container.querySelector(".tuner-status-line");
    assert.match(statusLine.textContent, /Host does not support live tool re-invocation/);

    const slider = container.querySelector('input[type="range"]');
    slider.value = "30";
    slider.dispatchEvent(new window.Event("input"));

    // Give any (incorrectly) scheduled debounce+recompute a full window to
    // fire. If the bug regressed, the status line would flip to
    // "Recalculating…" (or later "Error: ...", since `app.callServerTool`
    // isn't a function) despite the widget claiming static-only rendering.
    await new Promise((resolve) => window.setTimeout(resolve, 500));

    assert.match(statusLine.textContent, /Host does not support live tool re-invocation/, "status line must remain the static message -- no recompute may have fired");
  });

  test("also works with no app argument at all (app === undefined) -- same static-display contract", () => {
    const { window, document } = freshCommonJs();
    const container = document.createElement("div");
    window.renderStrategyTuner(container, { strategy_name: "x" }, undefined);
    assert.match(container.querySelector(".tuner-status-line").textContent, /Host does not support live tool re-invocation/);
  });
});
