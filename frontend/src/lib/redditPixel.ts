import { Platform } from 'react-native';

// Reddit Ads conversion pixel — web-only, and deliberately loaded ONLY from the signed-out
// marketing/signup flow (landing.tsx, google-auth.tsx), never from +html.tsx or anywhere in
// the authed app. This app's users are largely minors; scoping the pixel to the pages an ad
// click actually lands on measures ad -> signup conversion without handing Reddit an ongoing
// feed of a logged-in student's in-app activity. See legal/privacy.md §7 for the disclosure
// this requires alongside Microsoft Clarity.
//
// EXPO_PUBLIC_REDDIT_PIXEL_ID is baked in at build time like EXPO_PUBLIC_API_BASE
// (httpClient.ts). Unset in dev/mock: every call here is a silent no-op, same convention as
// GEMINI_API_KEY/ANTHROPIC_API_KEY being absent.
const PIXEL_ID = (process.env.EXPO_PUBLIC_REDDIT_PIXEL_ID ?? '').trim();

type RdtFn = (...args: unknown[]) => void;

function rdt(): RdtFn | null {
  if (Platform.OS !== 'web' || !PIXEL_ID) return null;
  const w = globalThis as unknown as { rdt?: RdtFn };
  return typeof w.rdt === 'function' ? w.rdt : null;
}

let loadStarted = false;

// Injects Reddit's pixel loader once and calls init. Safe to call multiple times (idempotent)
// and safe to call before the script has finished downloading — the loader defines `rdt` as a
// synchronous queuing stub, same pattern as the Clarity snippet in +html.tsx.
export function loadRedditPixel(): void {
  if (Platform.OS !== 'web' || !PIXEL_ID || loadStarted) return;
  loadStarted = true;
  try {
    const w = globalThis as unknown as { rdt?: RdtFn };
    if (typeof w.rdt !== 'function') {
      const stub: RdtFn & { callQueue?: unknown[]; sendEvent?: RdtFn } = ((...args: unknown[]) => {
        (stub.callQueue = stub.callQueue || []).push(args);
      }) as RdtFn & { callQueue?: unknown[]; sendEvent?: RdtFn };
      stub.callQueue = [];
      stub.sendEvent = stub;
      w.rdt = stub;
      const script = document.createElement('script');
      script.async = true;
      script.src = 'https://www.redditstatic.com/ads/pixel.js';
      document.head.appendChild(script);
    }
    w.rdt?.('init', PIXEL_ID);
  } catch {
    /* analytics must never break the app */
  }
}

// Fires once a NEW account is actually created (google-auth.tsx / apple-auth.tsx's `finish()`
// succeeding) — never on an ordinary sign-in of an existing account, or this would overstate
// conversions on every return visit.
export function trackSignUp(): void {
  try {
    rdt()?.('track', 'SignUp');
  } catch {
    /* noop */
  }
}
