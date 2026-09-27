import { Platform } from 'react-native';

// Google Ads tag (gtag.js) — web-only, and deliberately loaded ONLY from the signed-out
// marketing/signup flow (landing.tsx, google-auth.tsx), never from +html.tsx or anywhere in
// the authed app. Same reasoning as the Reddit Ads pixel (redditPixel.ts): this app's users
// are largely minors, so scoping the ad tag to the pages an ad click actually lands on
// measures ad -> signup conversion without handing Google an ongoing feed of a logged-in
// student's in-app activity. This is why we do NOT follow Google's paste-on-every-page
// instruction. See legal/privacy.md §7 for the disclosure this requires alongside Microsoft
// Clarity and the Reddit pixel.
//
// EXPO_PUBLIC_GOOGLE_ADS_ID is baked in at build time like EXPO_PUBLIC_REDDIT_PIXEL_ID
// (redditPixel.ts). Unset in dev/mock: every call here is a silent no-op, same convention as
// GEMINI_API_KEY/ANTHROPIC_API_KEY being absent. EXPO_PUBLIC_GOOGLE_ADS_SIGNUP_LABEL is the
// optional per-conversion label ("AW-XXX/abc123") a signup fires against — leave it unset and
// the tag still loads for remarketing/measurement, the SignUp conversion just doesn't fire.
const ADS_ID = (process.env.EXPO_PUBLIC_GOOGLE_ADS_ID ?? '').trim();
const SIGNUP_LABEL = (process.env.EXPO_PUBLIC_GOOGLE_ADS_SIGNUP_LABEL ?? '').trim();

type GtagFn = (...args: unknown[]) => void;

function gtag(): GtagFn | null {
  if (Platform.OS !== 'web' || !ADS_ID) return null;
  const w = globalThis as unknown as { gtag?: GtagFn };
  return typeof w.gtag === 'function' ? w.gtag : null;
}

let loadStarted = false;

// Injects Google's gtag.js loader once and runs the standard config. Safe to call multiple
// times (idempotent) and safe to call before the script has finished downloading — gtag is
// defined as a synchronous dataLayer-queuing stub first, exactly as Google's own snippet does
// and mirroring the Clarity snippet in +html.tsx.
export function loadGoogleAdsTag(): void {
  if (Platform.OS !== 'web' || !ADS_ID || loadStarted) return;
  loadStarted = true;
  try {
    const w = globalThis as unknown as { gtag?: GtagFn; dataLayer?: unknown[] };
    if (typeof w.gtag !== 'function') {
      w.dataLayer = w.dataLayer || [];
      const stub: GtagFn = (...args: unknown[]) => {
        (w.dataLayer as unknown[]).push(args);
      };
      w.gtag = stub;
      const script = document.createElement('script');
      script.async = true;
      script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(ADS_ID)}`;
      document.head.appendChild(script);
    }
    w.gtag?.('js', new Date());
    w.gtag?.('config', ADS_ID);
  } catch {
    /* analytics must never break the app */
  }
}

// Fires once a NEW account is actually created (google-auth.tsx / apple-auth.tsx's `finish()`
// succeeding) — never on an ordinary sign-in of an existing account, or this would overstate
// conversions on every return visit. No-op until EXPO_PUBLIC_GOOGLE_ADS_SIGNUP_LABEL is set,
// since Google Ads needs the conversion action's label ("AW-XXX/abc123") to attribute it.
export function trackSignUp(): void {
  if (!SIGNUP_LABEL) return;
  try {
    gtag()?.('event', 'conversion', { send_to: SIGNUP_LABEL });
  } catch {
    /* noop */
  }
}
