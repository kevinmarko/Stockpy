import { createRequire } from "node:module";
import { readFileSync, writeFileSync } from "node:fs";

const require = createRequire(import.meta.url);
const raw = readFileSync(require.resolve("@modelcontextprotocol/ext-apps/app-with-deps"), "utf8");

const rewritten = raw.replace(/export\{([^}]+)\};?\s*$/, (_, body) =>
  "globalThis.ExtApps={" +
  body.split(",").map((p) => {
    const [local, exported] = p.split(" as ").map((s) => s.trim());
    return `${exported ?? local}:${local}`;
  }).join(",") + "};"
);

writeFileSync("../vendor/ext-apps-bundle.js", rewritten);
console.log("Wrote mcp_widgets/vendor/ext-apps-bundle.js (" + rewritten.length + " bytes)");
