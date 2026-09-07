import { Platform } from 'react-native';

// Microsoft Clarity custom-event wrapper (project yadv9a58um).
//
// The Clarity loader is injected in app/+html.tsx and exists ONLY on the web build — on
// iOS/Android `window.clarity` is undefined, so every call here must be guarded or it would
// throw on native. The loader defines `window.clarity` as a queuing stub synchronously, so
// calls made before the async tag finishes downloading are queued rather than lost.
//
// PRIVACY (this app's users are largely minors): never pass PII or anything free-text a
// student typed (search descriptions, chat drafts, names, emails) as an event name or tag
// value. Track that an action HAPPENED, never its content. `identify` takes the opaque
// account `userid` only.

type ClarityFn = (cmd: string, ...args: unknown[]) => void;

function clarity(): ClarityFn | null {
  if (Platform.OS !== 'web') return null;
  const c = (globalThis as { clarity?: unknown }).clarity;
  return typeof c === 'function' ? (c as ClarityFn) : null;
}

// A custom event — a filterable signal in the Clarity dashboard. Name only; no payload.
export function trackEvent(name: string): void {
  try {
    clarity()?.('event', name);
  } catch {
    /* analytics must never break the app */
  }
}

// A custom tag — a segmentation dimension. Use opaque values only (plan tier, grade band).
export function setTag(key: string, value: string | string[]): void {
  try {
    clarity()?.('set', key, value);
  } catch {
    /* noop */
  }
}

// Tie sessions to an account. Pass the opaque `userid` — never email or name.
export function identify(userid: string): void {
  if (!userid) return;
  try {
    clarity()?.('identify', userid);
  } catch {
    /* noop */
  }
}

// Flag a high-value session (checkout, account deletion, a caught error) so Clarity keeps the
// recording even under sampling.
export function upgradeSession(reason: string): void {
  try {
    clarity()?.('upgrade', reason);
  } catch {
    /* noop */
  }
}
