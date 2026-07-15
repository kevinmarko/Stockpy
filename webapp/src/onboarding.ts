/** Client-side onboarding completion marker (mirrors gui/onboarding.py concept). */

const KEY = "stockpy.onboarding.v1";

export interface OnboardingState {
  completed: boolean;
  pilotId?: string;
  brokerage?: "paper" | "robinhood" | "skip";
  amount?: number;
  completedAt?: string;
}

export function readOnboarding(): OnboardingState {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { completed: false };
    return JSON.parse(raw) as OnboardingState;
  } catch {
    return { completed: false };
  }
}

export function writeOnboarding(state: OnboardingState): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

export function completeOnboarding(partial: Partial<OnboardingState>): void {
  writeOnboarding({
    ...readOnboarding(),
    ...partial,
    completed: true,
    completedAt: new Date().toISOString(),
  });
}

export function resetOnboarding(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
