import * as AppleAuthentication from 'expo-apple-authentication';
import { Platform } from 'react-native';
import type { AppleCredential } from '@/api/types';

// Sign in with Apple — native iOS only (App Store 4.8). Apple's SDK returns a signed identity
// token ON DEVICE, so there is no browser redirect and no backend handoff nonce like Google's:
// beginAppleSignIn() hands the caller the token directly and the app POSTs it to
// /api/auth/apple/native. Never used on web/Android — Apple's button is iOS-only in login.tsx.

export function isAppleSignInSupported(): boolean {
  return Platform.OS === 'ios';
}

// Opens the native Apple sheet. Returns the credential, or null if the user cancelled. The
// name comes back ONLY on the first authorization for this app, so it may be absent on a later
// sign-in — the backend keeps whatever it was given the first time.
export async function beginAppleSignIn(): Promise<AppleCredential | null> {
  if (Platform.OS !== 'ios') return null;
  try {
    const credential = await AppleAuthentication.signInAsync({
      requestedScopes: [
        AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
        AppleAuthentication.AppleAuthenticationScope.EMAIL,
      ],
    });
    // No identity token means nothing the backend can verify — treat as a failed attempt.
    if (!credential.identityToken) return null;
    return {
      identityToken: credential.identityToken,
      firstName: credential.fullName?.givenName ?? undefined,
      lastName: credential.fullName?.familyName ?? undefined,
    };
  } catch (e) {
    // The user backing out of the sheet is not an error to surface.
    if ((e as { code?: string }).code === 'ERR_REQUEST_CANCELED') return null;
    throw e;
  }
}

// A new Apple account needs signup consent, which is collected on the /apple-auth screen. The
// screen is reached by a router.replace (no useful params for a big JWT), so the credential and
// the pending identity it carried are stashed here for that screen to pick up — mirroring how
// google-auth reads its handoff at the boundary. One-shot: taking it clears it.
export interface ApplePending {
  credential: AppleCredential;
  firstName?: string;
  lastName?: string;
  email?: string;
}

let _pending: ApplePending | null = null;

export function stashApplePending(pending: ApplePending): void {
  _pending = pending;
}

export function takeApplePending(): ApplePending | null {
  const p = _pending;
  _pending = null;
  return p;
}
