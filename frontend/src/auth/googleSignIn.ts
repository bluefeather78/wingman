import * as Linking from 'expo-linking';
import * as WebBrowser from 'expo-web-browser';
import { Platform } from 'react-native';
import { httpClient } from '@/api/httpClient';

// Kicks off the Google redirect flow. The app passes its OWN redirect URI to the backend's
// /start; the backend (Phase 3 change) sends the one-time google_token there rather than to
// its own SPA root. Web uses a full-page redirect (reliable, matches the eventual Render
// static-site deploy); native opens a system auth session that closes on the redirect.
WebBrowser.maybeCompleteAuthSession();

export function googleRedirectUri(): string {
  if (Platform.OS === 'web') {
    return `${globalThis.location.origin}/google-auth`;
  }
  // e.g. wingman://google-auth (dev build) or exp://…/--/google-auth (Expo Go).
  return Linking.createURL('google-auth');
}

// Where the Google *Calendar* connect flow should return to. Separate grant, separate
// landing: the student pressed Sync in the Quest Log, so that is where they come back to.
// Must stay covered by the backend's GOOGLE_APP_REDIRECTS allowlist.
export function googleCalendarReturnUri(): string {
  if (Platform.OS === 'web') {
    return `${globalThis.location.origin}/tracker`;
  }
  return Linking.createURL('tracker');
}

// One key out of a `a=1&b=2` blob. Hand-rolled rather than URLSearchParams: React Native's
// polyfill for that is famously partial (`.get()` is missing on some versions), and this
// runs on the sign-in path, which is the last place to discover a missing method.
function paramFrom(blob: string, key: string): string | null {
  for (const pair of blob.split('&')) {
    if (!pair) continue;
    const eq = pair.indexOf('=');
    const rawKey = eq === -1 ? pair : pair.slice(0, eq);
    if (decodeURIComponent(rawKey) !== key) continue;
    if (eq === -1) return '';
    return decodeURIComponent(pair.slice(eq + 1).replace(/\+/g, ' '));
  }
  return null;
}

// The one-time sign-in token out of a redirect URL — FRAGMENT FIRST (S0-9).
//
// The server puts it in the fragment for an http(s) destination, because that redirect hits
// our own origin and a query string there lands in Render's access log, in browser history,
// and in the Referer of everything the page then loads. It keeps the query string for a
// custom scheme, where the OS resolves the redirect and no HTTP server ever sees it.
//
// Both are read here regardless of platform: which form arrives is the server's decision,
// not this function's, and reading only the one we expect would turn a server-side change
// into a broken sign-in.
export function googleHandoffFromUrl(url: string): string | null {
  const [beforeHash, afterHash = ''] = url.split('#');
  const fromFragment = paramFrom(afterHash, 'google_token');
  if (fromFragment) return fromFragment;
  return paramFrom(beforeHash.split('?')[1] ?? '', 'google_token');
}

// On web: navigates away and never returns (the /google-auth route resumes the flow).
// On native: returns the one-time handoff token, or null if the user cancelled.
export async function beginGoogleSignIn(): Promise<string | null> {
  const redirectUri = googleRedirectUri();
  const startUrl = httpClient.googleStartUrl(redirectUri);

  if (Platform.OS === 'web') {
    globalThis.location.href = startUrl;
    return null;
  }

  const result = await WebBrowser.openAuthSessionAsync(startUrl, redirectUri);
  if (result.type !== 'success' || !result.url) return null;
  // Parsed off the raw URL rather than through Linking.parse: that returns queryParams only,
  // so it would silently drop a fragment if the server ever sent one to a custom scheme.
  return googleHandoffFromUrl(result.url);
}
