/**
 * Presentation logic for the settings editors' per-field liveness metadata.
 *
 * Pure functions only — no React, no API calls — so the interesting decisions
 * (what a field's badge says, whether its input is editable, which dangerous
 * keys a save must confirm, what the post-save message should be) are unit
 * testable without rendering anything. Same split this codebase already uses
 * for `optionsHonesty.ts` / `marketSession.ts` / `brokerageLoginCopy.ts`.
 *
 * The one rule everything here follows: **never claim a change took effect
 * that did not.** Where the data does not say, the fallback is the
 * conservative "you still need to restart", which is what the backend itself
 * reports for a field it cannot classify.
 */

import type {
  AppliesState,
  AppliesSummary,
  TunableField,
  TunableLiveness,
  TunablesUpdateResult,
} from "./api/types";

/**
 * The fallback used when a field carries no `liveness` at all (an older
 * backend, or a hand-written test fixture).
 *
 * Deliberately mirrors `pilots/settings_meta.py`'s unknown-classification
 * behaviour rather than inventing a fifth "unknown" state for the UI: a field
 * whose behaviour cannot be established is reported as needing a restart,
 * because that is what a plain `.env` write means and it is the direction that
 * cannot mislead. `dangerous: false` is safe here despite looking permissive —
 * the confirmation gate is enforced SERVER-SIDE, so the worst case is that the
 * UI omits a dialog and the server rejects the write with
 * `confirmation_required`, which the editor surfaces per key.
 */
export const FALLBACK_LIVENESS: TunableLiveness = {
  applies: "next_daemon_restart",
  restart_reason: null,
  capture_sites: [],
  env_pinned: false,
  dangerous: false,
  source: "env_file",
};

/** One field's liveness, with the conservative fallback applied. */
export function resolveLiveness(field: TunableField): TunableLiveness {
  return field.liveness ?? FALLBACK_LIVENESS;
}

/** Whether this field's input should be editable at all. */
export function isFieldEditable(field: TunableField): boolean {
  const lv = resolveLiveness(field);
  // `no_effect` stays editable on purpose: writing it is harmless and the
  // value is still durable in `.env`, so a field that a FUTURE build reads
  // can be set today. Only an env pin makes editing genuinely pointless,
  // because the shell export overrides whatever is written.
  return !lv.env_pinned;
}

export interface AppliesBadge {
  label: string;
  /** Maps onto index.css's `.badge-*` variant classes. */
  tone: "good" | "warn" | "bad" | "neutral";
  /** Longer explanation for an InfoTip. */
  title: string;
}

const BADGES: Record<AppliesState, AppliesBadge> = {
  immediately: {
    label: "Applies now",
    tone: "good",
    title:
      "Saving this takes effect in the running process straight away — no restart needed.",
  },
  next_daemon_restart: {
    label: "Needs restart",
    tone: "warn",
    title:
      "Saving this writes it to .env, but the running process keeps its current value until it restarts.",
  },
  no_effect: {
    label: "No effect",
    tone: "neutral",
    title:
      "Nothing in the platform currently reads this setting, so changing it has no effect anywhere.",
  },
  env_pinned: {
    label: "Pinned by environment",
    tone: "bad",
    title:
      "A shell environment variable is set for this field. That always wins, so this value cannot be changed here until the export is removed.",
  },
};

/** The badge for one `applies` state. */
export function appliesBadge(state: AppliesState): AppliesBadge {
  return BADGES[state] ?? BADGES.next_daemon_restart;
}

/** The badge for one field, via its resolved liveness. */
export function fieldBadge(field: TunableField): AppliesBadge {
  return appliesBadge(resolveLiveness(field).applies);
}

/**
 * The screen-level notice replacing the old blanket "Changes apply on the next
 * pipeline / daemon restart (no hot-reload)".
 *
 * `null` means say nothing at all — used when every field applies immediately,
 * where a restart notice would be actively wrong.
 */
export function screenAppliesNotice(
  summary: AppliesSummary | undefined,
  counts?: Partial<Record<AppliesState, number>>,
): { variant: "info" | "warn"; text: string } | null {
  const restartCount = counts?.next_daemon_restart ?? 0;
  const pinnedCount = counts?.env_pinned ?? 0;

  switch (summary) {
    case "immediately":
      return {
        variant: "info",
        text: "Changes on this screen apply to the running process immediately — no restart needed.",
      };
    case "no_effect":
      return {
        variant: "warn",
        text: "Nothing on this screen is currently read by the platform, so changes here have no effect.",
      };
    case "env_pinned":
      return {
        variant: "warn",
        text: "Every field on this screen is pinned by a shell environment variable and cannot be changed here.",
      };
    case "mixed": {
      const parts: string[] = [];
      if (restartCount > 0) {
        parts.push(
          `${restartCount} need${restartCount === 1 ? "s" : ""} a restart`,
        );
      }
      if (pinnedCount > 0) {
        parts.push(
          `${pinnedCount} ${pinnedCount === 1 ? "is" : "are"} pinned by the environment`,
        );
      }
      return {
        variant: "info",
        text: parts.length
          ? `Settings on this screen differ: ${parts.join(", ")}. Each field is labelled below.`
          : "Settings on this screen differ in when they take effect. Each field is labelled below.",
      };
    }
    case "next_daemon_restart":
      return {
        variant: "info",
        text: "Changes on this screen are saved to .env and picked up the next time the pipeline / daemon restarts.",
      };
    default:
      // No summary at all (older backend): the old blanket claim, which is the
      // correct thing to say when nothing better is known.
      return {
        variant: "info",
        text: "Changes are saved to .env and picked up the next time the pipeline / daemon restarts.",
      };
  }
}

/**
 * Which of the keys about to be written need an explicit confirmation.
 * Sorted so the dialog's wording is stable across renders.
 */
export function dangerousKeysIn(
  fields: TunableField[],
  keys: string[],
): string[] {
  const wanted = new Set(keys);
  return fields
    .filter((f) => wanted.has(f.key) && resolveLiveness(f).dangerous)
    .map((f) => f.key)
    .sort();
}

/**
 * The `confirm` map for a set of dangerous keys — each key echoing its own
 * name, which is the exact shape the backend validates against.
 */
export function buildConfirmMap(keys: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of keys) out[k] = k;
  return out;
}

/**
 * Post-save message, split so "applied now" and "saved, needs restart" are
 * never collapsed into one generic sentence.
 *
 * The backend's own `note` is preferred when present — it is computed from the
 * real per-key outcome and is the authoritative account of what happened. The
 * locally-derived sentences below are the fallback for an older backend that
 * sends no `note`.
 */
export function saveOutcomeMessage(
  result: Pick<TunablesUpdateResult, "written" | "per_key_applies" | "note">,
): { variant: "success" | "info"; text: string } | null {
  const writtenKeys = Object.keys(result.written ?? {});
  if (writtenKeys.length === 0) return null;

  const perKey = result.per_key_applies ?? {};
  const appliedNow = writtenKeys.filter((k) => perKey[k] === "immediately");
  const pending = writtenKeys.filter((k) => perKey[k] !== "immediately");

  if (result.note) {
    return {
      variant: appliedNow.length && !pending.length ? "success" : "info",
      text: result.note,
    };
  }

  if (appliedNow.length && !pending.length) {
    return {
      variant: "success",
      text: `Saved and applied to the running process: ${appliedNow.join(", ")}. No restart needed.`,
    };
  }
  if (pending.length && !appliedNow.length) {
    return {
      variant: "info",
      text: `Saved to .env: ${pending.join(", ")}. The running engine keeps the previous values until its next restart.`,
    };
  }
  return {
    variant: "info",
    text: `Saved. Applied now: ${appliedNow.join(", ")}. Needs a restart: ${pending.join(", ")}.`,
  };
}
