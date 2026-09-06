// Two-tier AI model — the client's single reading of "which tier is this account on".
// Derived, never stored: the source of truth is the subscription block the server sends on
// every login/refresh/status (in_paid_period + the ai_tier label). has_access is always true
// now (no lockout), so the tier is the axis the UI cares about.
import type { AllowanceSnapshot, SessionUser } from '@/api/types';

export function isPaidTier(user: SessionUser | null | undefined): boolean {
  const sub = user?.subscription;
  return sub?.ai_tier === 'paid' || sub?.in_paid_period === true;
}

// A subscription that has been cancelled but is still inside the period the student paid
// for (cancel-at-period-end — see cancel_subscription / subscription_state on the server).
// The account is still Paid until `subscription_end_at`, so it reverts to Free only then,
// not the moment Cancel was clicked. Returns the days left and the end date so the home
// page can show a "your plan ends in X days" upsell; null for every other account.
export function subscriptionEnding(
  user: SessionUser | null | undefined,
): { days: number; endAt?: string } | null {
  const sub = user?.subscription;
  if (sub?.status !== 'canceled' || sub?.in_paid_period !== true) return null;
  const days = typeof sub?.days_left === 'number' ? Math.max(0, sub.days_left) : 0;
  const endAt = typeof sub?.subscription_end_at === 'string' ? sub.subscription_end_at : undefined;
  return { days, endAt };
}

export function tierName(user: SessionUser | null | undefined): string {
  return isPaidTier(user) ? 'Wingman Unlimited' : 'Free plan';
}

export function tierShortName(user: SessionUser | null | undefined): string {
  return isPaidTier(user) ? 'Unlimited' : 'Free';
}

// A human "resets in 6h" from the allowance's reset_at ISO. Falls back to "at midnight UTC"
// when the timestamp is missing or unparseable — a wrong countdown is worse than none.
export function resetsInLabel(a: AllowanceSnapshot | null | undefined): string {
  const iso = a?.reset_at;
  if (!iso) return 'at midnight UTC';
  const when = Date.parse(String(iso));
  if (Number.isNaN(when)) return 'at midnight UTC';
  const mins = Math.max(0, Math.round((when - Date.now()) / 60000));
  if (mins < 60) return `in ${Math.max(1, mins)}m`;
  const hrs = Math.round(mins / 60);
  return `in ${hrs}h`;
}
