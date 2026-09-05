import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// frontend_report finding 4: "A transport failure during refresh is treated as 'revoked':
// refreshOnce returns false on catch, and both boot paths then forgetSession(). Offline at
// launch = logged out with a valid access token."
//
// The damage outlived the outage. forgetSession() DELETES the stored refresh token, so a
// student who opened the app on a train, in a lift, or behind a school captive portal was not
// just shown the login screen for a moment — their session was destroyed and they had to type
// a password to get it back.
//
// Three outcomes, and only one of them justifies destroying credentials:
//   ok           a fresh pair is in place
//   revoked      the server adjudicated: 401 or 403
//   unreachable  no answer, or a 5xx — which says nothing about this token's validity

const ACCESS_LIVE = makeJwt(Math.floor(Date.now() / 1000) + 3600);
const ACCESS_EXPIRED = makeJwt(Math.floor(Date.now() / 1000) - 3600);

function makeJwt(exp: number): string {
  const b64 = (o: unknown) =>
    Buffer.from(JSON.stringify(o)).toString('base64url');
  return `${b64({ alg: 'HS256' })}.${b64({ exp, sub: 'alice' })}.sig`;
}

/** A localStorage good enough for tokenStore's web path. */
function installStorage(seed: Record<string, string> = {}) {
  const store = new Map(Object.entries(seed));
  const storage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() { return store.size; },
  };
  vi.stubGlobal('localStorage', storage);
  vi.stubGlobal('window', { localStorage: storage });
  return store;
}

// The exact shape sessionFromPayload stores (httpClient.ts).
const USER = { userid: 'alice', firstName: 'Alice', lastName: 'B', email: 'a@example.com' };

// tokenStore's real keys — the access token, the refresh token and the identity are three
// separate entries, not one blob.
const ACCESS_KEY = 'wingman.access_token';
const REFRESH_KEY = 'wingman.refresh_token';
const SESSION_KEY = 'wingman.session_user';

const signedIn = (access: string) => ({
  [ACCESS_KEY]: access,
  [REFRESH_KEY]: 'r1',
  [SESSION_KEY]: JSON.stringify(USER),
});

/** Fresh module state per test — httpClient holds the session in module scope. */
async function freshClient() {
  vi.resetModules();
  const mod = await import('@/api/httpClient');
  const tokenStore = await import('@/api/tokenStore');
  return { httpClient: mod.httpClient, tokenStore };
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('initAuth on the FAST path — a live access token plus a cached identity', () => {
  it('keeps the session when the background refresh cannot reach the server', async () => {
    const store = installStorage(signedIn(ACCESS_LIVE));
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const { httpClient } = await freshClient();

    const user = await httpClient.initAuth();
    expect(user?.userid).toBe('alice');
    // Let the fire-and-forget refresh settle.
    await new Promise((r) => setTimeout(r, 0));
    expect(store.get(REFRESH_KEY)).toBeTruthy();
  });

  it('keeps the session on a 500 — a server error is not a verdict on the token', async () => {
    const store = installStorage(signedIn(ACCESS_LIVE));
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 500 })));
    const { httpClient } = await freshClient();

    await httpClient.initAuth();
    await new Promise((r) => setTimeout(r, 0));
    expect(store.get(REFRESH_KEY)).toBeTruthy();
  });

  it('DROPS the session when the server actually says 401', async () => {
    const store = installStorage(signedIn(ACCESS_LIVE));
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
    const { httpClient } = await freshClient();

    await httpClient.initAuth();
    await new Promise((r) => setTimeout(r, 0));
    expect(store.get(REFRESH_KEY)).toBeFalsy();
  });
});

describe('initAuth on the SLOW path — no cached identity, or an expired access token', () => {
  it('returns null but KEEPS the tokens when the server is unreachable', async () => {
    // There is genuinely no identity to render, so the router shows the signed-out screen —
    // but forgetting the tokens would make a temporary outage permanent.
    const store = installStorage({ [ACCESS_KEY]: ACCESS_EXPIRED, [REFRESH_KEY]: 'r1' });
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const { httpClient } = await freshClient();

    expect(await httpClient.initAuth()).toBeNull();
    expect(store.get(REFRESH_KEY)).toBeTruthy();
  });

  it('forgets the tokens when the refresh token is genuinely revoked', async () => {
    const store = installStorage({ [ACCESS_KEY]: ACCESS_EXPIRED, [REFRESH_KEY]: 'r1' });
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
    const { httpClient } = await freshClient();

    expect(await httpClient.initAuth()).toBeNull();
    expect(store.get(REFRESH_KEY)).toBeFalsy();
  });

  it('signs in when the refresh succeeds', async () => {
    installStorage({ [ACCESS_KEY]: ACCESS_EXPIRED, [REFRESH_KEY]: 'r1' });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ token: ACCESS_LIVE, refresh_token: 'r2', ...USER }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    )));
    const { httpClient } = await freshClient();

    const user = await httpClient.initAuth();
    expect(user?.userid).toBe('alice');
  });
});

describe('a 401 mid-session', () => {
  it('does not destroy the session when the refresh call itself cannot be reached', async () => {
    const store = installStorage(signedIn(ACCESS_LIVE));
    let call = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: unknown) => {
      call += 1;
      const url = String(input);
      if (url.includes('/api/auth/refresh')) throw new TypeError('Failed to fetch');
      return new Response('{}', { status: 401 });
    }));
    const { httpClient } = await freshClient();
    await httpClient.initAuth();
    await new Promise((r) => setTimeout(r, 0));

    await expect(httpClient.loadData('hs-tracker-data')).rejects.toThrow(/could not reach/i);
    expect(store.get(REFRESH_KEY)).toBeTruthy();
    expect(call).toBeGreaterThan(0);
  });

  it('still ends the session when the refresh token is refused', async () => {
    const store = installStorage(signedIn(ACCESS_LIVE));
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
    const { httpClient } = await freshClient();
    await httpClient.initAuth();
    await new Promise((r) => setTimeout(r, 0));

    await expect(httpClient.loadData('hs-tracker-data')).rejects.toThrow();
    expect(store.get(REFRESH_KEY)).toBeFalsy();
  });
});
