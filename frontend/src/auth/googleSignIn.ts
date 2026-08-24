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
  const parsed = Linking.parse(result.url);
  const token = parsed.queryParams?.google_token;
  return typeof token === 'string' ? token : null;
}
