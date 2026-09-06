// Two-tier AI model — the client's single reading of "which tier is this account on".
// Derived, never stored: the source of truth is the subscription block the server sends on
// every login/refresh/status (in_paid_period + the ai_tier label). has_access is always true
// now (no lockout), so the tier is the axis the UI cares about.
import type { AllowanceSnapshot, SessionUser } from '@/api/types';

export function isPaidTier(user: SessionUser | null | undefined): boolean {
  const sub = user?.subscription;
  return sub?.ai_tier === 'paid' || sub?.in_paid_period === true;
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
