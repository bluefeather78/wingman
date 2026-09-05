import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// MARQUEE M9 (Phase 5, frontend_report finding 5): "No request timeouts / AbortController
// anywhere; a hung fetch hangs the deadline-refresh loop forever."
//
// `fetch` has no default timeout in any of the three runtimes this app ships to. A connection
// that opens and then goes silent — a captive portal, a proxy holding the socket, a Render
// instance that accepted the request and died — never settles, so the promise never resolves
// AND never rejects. The Quest Log's refresh awaits each item in turn, so one such request
// stops the whole pass forever with the spinner still turning, and there is nothing for the
// student to do but force-quit.

const ACCESS_KEY = 'wingman.access_token';
const REFRESH_KEY = 'wingman.refresh_token';
const SESSION_KEY = 'wingman.session_user';
const USER = { userid: 'alice', firstName: 'Alice', lastName: 'B', email: 'a@example.com' };

function makeJwt(exp: number): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url');
  return `${b64({ alg: 'HS256' })}.${b64({ exp, sub: 'alice' })}.sig`;
}

function installStorage(seed: Record<string, string>) {
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

/** A fetch that NEVER settles until its signal aborts — the failure mode under test. */
function hangingFetch(seen: { signals: AbortSignal[] }) {
  return vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    const signal = init?.signal;
    if (signal) {
      seen.signals.push(signal);
      signal.addEventListener('abort', () => {
        const err = new Error('The operation was aborted.');
        err.name = 'AbortError';
        reject(err);
      });
    }
  }));
}

async function signedInClient() {
  vi.resetModules();
  installStorage({
    [ACCESS_KEY]: makeJwt(Math.floor(Date.now() / 1000) + 3600),
    [REFRESH_KEY]: 'r1',
    [SESSION_KEY]: JSON.stringify(USER),
  });
  const mod = await import('@/api/httpClient');
  await mod.httpClient.initAuth();
  return mod;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('every request carries an abort signal', () => {
  it('a hung ordinary request rejects instead of hanging forever', async () => {
    const seen = { signals: [] as AbortSignal[] };
    vi.stubGlobal('fetch', hangingFetch(seen));
    const { httpClient, REQUEST_TIMEOUT_MS } = await signedInClient();
    vi.useFakeTimers();

    const pending = httpClient.loadData('hs-tracker-data');
    const settled = pending.then(() => 'resolved', (e: Error) => e.message);
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 100);
    await expect(settled).resolves.toMatch(/took too long/i);
    expect(seen.signals.length).toBeGreaterThan(0);
  });

  it('does not abort a request that is merely slow but still inside the ceiling', async () => {
    const seen = { signals: [] as AbortSignal[] };
    vi.stubGlobal('fetch', hangingFetch(seen));
    const { httpClient, REQUEST_TIMEOUT_MS } = await signedInClient();
    vi.useFakeTimers();

    const settled = httpClient.loadData('hs-tracker-data').then(() => 'resolved', () => 'rejected');
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS - 1000);
    expect(seen.signals[0].aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(2000);
    await settled;
  });

  it('gives a model-backed call the longer ceiling, not the ordinary one', async () => {
    // A deadline check runs several upstream searches and is genuinely a minutes-scale
    // request. Holding it to the ordinary timeout would abort real work.
    const seen = { signals: [] as AbortSignal[] };
    vi.stubGlobal('fetch', hangingFetch(seen));
    const { httpClient, REQUEST_TIMEOUT_MS, AI_TIMEOUT_MS } = await signedInClient();
    vi.useFakeTimers();

    const settled = httpClient.getDeadlineCheck('ec1').then(() => 'resolved', () => 'rejected');
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 1000);
    expect(seen.signals[0].aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(AI_TIMEOUT_MS);
    await settled;
  });

  it('the AI ceiling is longer than the ordinary one, and both are set', async () => {
    const { REQUEST_TIMEOUT_MS, AI_TIMEOUT_MS } = await import('@/api/httpClient');
    expect(REQUEST_TIMEOUT_MS).toBeGreaterThan(30_000);   // Render Free's cold start is ~30s
    expect(AI_TIMEOUT_MS).toBeGreaterThan(REQUEST_TIMEOUT_MS);
  });
});
