import type { OptionsDirective } from "../api/types";

/**
 * Default cap on how many symbols' worth of directives get summarized into
 * the chat context string. The Options Matrix screen can carry dozens of
 * directives; an uncapped join would balloon the prompt sent to the
 * Gemini/Anthropic chat backend (see api/data_api.py::chat_endpoint's new
 * `context` field) for no real benefit -- a chat question is almost always
 * about "the top N" rather than the entire universe.
 */
const DEFAULT_MAX_SYMBOLS = 30;

function fmtNumber(value: number | null | undefined, digits = 2): string | null {
  if (value == null || Number.isNaN(value)) return null;
  return value.toFixed(digits);
}

/**
 * Formats a concise, LLM-readable, one-line-per-symbol summary of the
 * currently displayed options directives -- symbol, Altman Z, days to
 * earnings, earnings risk, and net debt/EBITDA (the fields the Options
 * Matrix's fundamental-health badges show). Intended to be passed as
 * `context` in the chat request body so a question like "which of these
 * have earnings risk?" is grounded in what's actually on screen, without
 * the backend needing to re-fetch anything.
 *
 * Fields that are null/undefined for a given directive are simply omitted
 * from that line rather than rendered as a fabricated placeholder
 * (CONSTRAINT #4 -- never fabricate data).
 *
 * Returns an empty string when there are no directives to summarize, so a
 * caller can pass the result straight through to `openChat()`/`context`
 * without an extra empty check.
 */
export function buildOptionsContextText(
  directives: OptionsDirective[],
  maxSymbols: number = DEFAULT_MAX_SYMBOLS
): string {
  if (!directives || directives.length === 0) return "";

  const shown = directives.slice(0, maxSymbols);
  const lines = shown.map((d) => {
    const parts: string[] = [d.Symbol];

    if (d.Strategy) parts.push(`strategy=${d.Strategy}`);

    const altmanZ = fmtNumber(d.Altman_Z_Score);
    if (altmanZ != null) parts.push(`AltmanZ=${altmanZ}`);

    if (d.Days_To_Earnings != null) parts.push(`daysToEarnings=${d.Days_To_Earnings}`);

    if (d.Earnings_Risk != null) parts.push(`earningsRisk=${d.Earnings_Risk ? "yes" : "no"}`);

    const netDebtEbitda = fmtNumber(d.Net_Debt_EBITDA);
    if (netDebtEbitda != null) parts.push(`netDebtEBITDA=${netDebtEbitda}`);

    return parts.join(", ");
  });

  const truncatedNote =
    directives.length > shown.length ? ` (showing ${shown.length} of ${directives.length})` : "";
  const header = `Currently displayed options directives${truncatedNote}:`;

  return [header, ...lines].join("\n");
}
