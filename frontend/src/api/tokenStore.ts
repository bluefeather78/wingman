import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

// Persistent storage for the Phase 2 token pair.
//   - Native: expo-secure-store (Keychain / Keystore) — the proper secure storage the old
//     web `window.storage` couldn't offer.
//   - Web: localStorage (SecureStore is unavailable there). This mirrors what the updated
//     script.js client does per the Phase 2 result ("token storage in localStorage").
// Keys must be alphanumeric + ._- for SecureStore.
const ACCESS_KEY = 'wingman.access_token';
const REFRESH_KEY = 'wingman.refresh_token';

const isWeb = Platform.OS === 'web';

async function getItem(key: string): Promise<string | null> {
  if (isWeb) {
    try {
      return globalThis.localStorage?.getItem(key) ?? null;
    } catch {
      return null;
    }
  }
  return SecureStore.getItemAsync(key);
}

async function setItem(key: string, value: string): Promise<void> {
  if (isWeb) {
    try {
      globalThis.localStorage?.setItem(key, value);
    } catch {
      /* storage unavailable (private mode etc.) — token stays in memory only */
    }
    return;
  }
  await SecureStore.setItemAsync(key, value);
}

async function deleteItem(key: string): Promise<void> {
  if (isWeb) {
    try {
      globalThis.localStorage?.removeItem(key);
    } catch {
      /* ignore */
    }
    return;
  }
  await SecureStore.deleteItemAsync(key);
}

export interface TokenPair {
  access: string;
  refresh: string;
}

export async function loadTokens(): Promise<TokenPair | null> {
  const [access, refresh] = await Promise.all([getItem(ACCESS_KEY), getItem(REFRESH_KEY)]);
  if (access && refresh) return { access, refresh };
  return null;
}

export async function saveTokens(pair: TokenPair): Promise<void> {
  await Promise.all([setItem(ACCESS_KEY, pair.access), setItem(REFRESH_KEY, pair.refresh)]);
}

export async function clearTokens(): Promise<void> {
  await Promise.all([deleteItem(ACCESS_KEY), deleteItem(REFRESH_KEY)]);
}
