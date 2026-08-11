import { createRequire } from "node:module";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const raw = readFileSync(require.resolve("@modelcontextprotocol/ext-apps/app-with-deps"), "utf8");

const rewritten = raw.replace(/export\{([^}]+)\};?\s*$/, (_, body) =>
  "globalThis.ExtApps={" +
  body.split(",").map((p) => {
    const [local, exported] = p.split(" as ").map((s) => s.trim());
    return `${exported ?? local}:${local}`;
  }).join(",") + "};"
);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const chartJsPath = path.resolve(__dirname, "node_modules/chart.js/dist/chart.umd.js");
const chartJsCode = readFileSync(chartJsPath, "utf8");

const combined = chartJsCode + "\n\n" + rewritten;

writeFileSync("../vendor/ext-apps-bundle.js", combined);
console.log("Wrote mcp_widgets/vendor/ext-apps-bundle.js (" + combined.length + " bytes)");
