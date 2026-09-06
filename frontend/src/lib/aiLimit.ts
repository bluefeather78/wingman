// The Free-tier "out of AI actions for today" state, and the dismiss/reopen wiring for the
// banner that announces it (mockup: AI Limit Reached - Modal).
//
// Two pieces, deliberately together because they are one concept:
//   1. aiLimitReached(user, allowance) — is this account currently out of its daily AI
//      actions? Free tier only; Paid/Unlimited never is.
//   2. A module singleton tracking whether the student has DISMISSED the banner for the
//      current occurrence, with a pub/sub so <AiLimitBanner/> re-renders when it flips. The
//      banner is dismissible (✕) but MUST reappear whenever the student next initiates an AI
//      action — so every gated control calls reopenAiLimitBanner() when tapped while blocked.
import type { AllowanceSnapshot, SessionUser } from '@/api/types';
import { isPaidTier } from './tier';

// True when a Free account has spent its daily AI allowance. We need a KNOWN allowance with a
// numeric remaining of 0 — a null/unknown snapshot is not "reached" (never grey the app out on
// missing data), and unlimited/paid is never reached.
export function aiLimitReached(
  user: SessionUser | null | undefined,
  allowance: AllowanceSnapshot | null | undefined,
): boolean {
  if (isPaidTier(user)) return false;
  if (!allowance || allowance.unlimited) return false;
  const remaining = typeof allowance.remaining === 'number' ? allowance.remaining : null;
  return remaining !== null && remaining <= 0;
}

// ---- Banner dismiss state (module singleton + pub/sub) ----
// Dismissed hides the banner until an AI action reopens it. Not persisted: a dismissal is for
// "right now", and a fresh load that is still over quota should surface it again.
let _dismissed = false;
const _listeners = new Set<() => void>();

function _notify(): void {
  for (const listener of _listeners) listener();
}

export function subscribeAiLimitBanner(listener: () => void): () => void {
  _listeners.add(listener);
  return () => _listeners.delete(listener);
}

export function isAiLimitBannerDismissed(): boolean {
  return _dismissed;
}

// The ✕ on the banner.
export function dismissAiLimitBanner(): void {
  if (_dismissed) return;
  _dismissed = true;
  _notify();
}

// Bring the banner back. Called (a) when the student taps a greyed-out AI control while out of
// quota, and (b) by the banner itself the moment the allowance first hits zero.
export function reopenAiLimitBanner(): void {
  if (!_dismissed) return;
  _dismissed = false;
  _notify();
}
