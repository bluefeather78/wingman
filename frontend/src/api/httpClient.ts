import { AuthExpiredError, type ActionItemsResponse, type ApiClient, type CalendarSyncResult, type EventInput, type UserOpportunitySubmission, type WingmanEventAction } from './ApiClient';
import type { TrackerInfo } from '@/lib/tracker';
import { sha256Hex } from './hash';
import { clearSession, clearTokens, loadSession, loadTokens, saveSession, saveTokens } from './tokenStore';
import type {
  AiResponse,
  AiResult,
  GoogleFinishInput,
  GoogleSessionResult,
  LoginResponse,
  Opportunity,
  RegisterInput,
  SessionUser,
} from './types';

// Base URL for the FastAPI backend. On web dev it can be same-origin ('' -> /api/...);
// native + the Render static site need an absolute URL (EXPO_PUBLIC_API_BASE). Render's
// `fromService` supplies a bare hostname, so prepend https:// when no scheme is present,
// and drop any trailing slash so `${API_BASE}${path}` never double-slashes.
const RAW_API_BASE = (process.env.EXPO_PUBLIC_API_BASE ?? '').trim().replace(/\/$/, '');
const API_BASE = RAW_API_BASE && !/^https?:\/\//i.test(RAW_API_BASE) ? `https://${RAW_API_BASE}` : RAW_API_BASE;

// Absolute URL on the backend host — for static pages the backend serves (terms.html,
// privacy.html, about.html), which the account drawer links to.
export function backendUrl(path: string): string {
  return `${API_BASE}${path}`;
}

// --- In-memory session state (persisted via tokenStore) ---------------------
let _access: string | null = null;
let _refresh: string | null = null;
let _currentUser: SessionUser | null = null;
// Shared so concurrent 401s trigger exactly one refresh, not one each.
let _refreshInFlight: Promise<boolean> | null = null;

// Fires when a session we booted from cache turns out to be dead (see initAuth).
const _sessionLostListeners = new Set<() => void>();
// Fires when the identity we hold CHANGES without a new login — the background refresh
// coming back with a fresh subscription block, or a 402 telling us access has lapsed.
// AuthContext turns this into setUser, which is what makes the paywall appear without a
// reload. Without it a cached SessionUser could keep saying has_access:true for as long as
// the app stayed open, and every screen would just fail with 402s it did not explain.
const _userChangedListeners = new Set<(u: SessionUser | null) => void>();

function notifyUserChanged(): void {
  for (const listener of _userChangedListeners) listener(_currentUser);
}

// The subscription gate answers 402 from every route that IS the app (app/deps.py's
// require_subscription). Rather than let each call site interpret that, record it once on
// the cached identity: the (app) layout reads has_access and redirects to the paywall.
// This is a MIRROR of the server's decision, never the decision itself — the server
// re-derives it from subscription_state() on every request.
function markSubscriptionBlocked(): void {
  if (!_currentUser) return;
  const sub = _currentUser.subscription;
  if (sub?.has_access === false) return;
  _currentUser = { ..._currentUser, subscription: { ...(sub ?? {}), has_access: false } };
  void saveSession(_currentUser);
  notifyUserChanged();
}

// --- /api/data/load: one request per tick, and a read-through cache ---------
//
// Every key the app stores lives in the SAME `data` jsonb on the SAME row, so asking for
// three of them separately made the server fetch that row three times — and because the
// backend's Supabase calls are blocking, those "parallel" requests were serialized end to
// end (measured 2026-08-24: 164ms each alone, 660ms wall for Home Base's three).
//
// So loads COALESCE: every loadData() in one tick joins a batch that goes out as a single
// {keys:[...]} request. This is deliberately inside the client rather than at the call
// sites — Home Base's three, the Quest Log's two and the calendar sweep's two are all
// issued in one synchronous tick already (an async function runs to its first await
// synchronously), so they all collapse with no screen changing a line.
//
// The cache is the second half: values are kept per key so a re-focus can paint from the
// last known value instead of spinning for a round trip. It is a RENDER accelerator, not a
// source of truth — peekData() never replaces the fetch, it only lets a screen show
// something while the fetch is in flight. saveData writes through so our own writes can
// never leave it stale, and forgetSession clears it so the next account starts clean.
const _dataCache = new Map<string, unknown>();
type PendingLoad = { resolve: (v: unknown) => void; reject: (e: unknown) => void };
let _pendingLoads: Map<string, PendingLoad[]> | null = null;

function flushLoads(): void {
  const batch = _pendingLoads;
  _pendingLoads = null;
  if (!batch) return;
  const keys = [...batch.keys()];
  request<{ values?: Record<string, unknown>; value?: unknown }>('/api/data/load', {
    method: 'POST',
    // A single key still goes out as {keys:[k]} — the server answers {values} either way,
    // and one shape here means one code path to reason about.
    body: JSON.stringify({ keys }),
  })
    .then((data) => {
      const values = data.values ?? {};
      for (const [key, waiters] of batch) {
        const value = values[key] ?? null;
        _dataCache.set(key, value);
        for (const w of waiters) w.resolve(value);
      }
    })
    .catch((err) => {
      // Every waiter in the batch gets the real error — an unreadable tracker and a dead
      // network must stay distinguishable downstream (loadTrackerDataChecked depends on it).
      for (const waiters of batch.values()) for (const w of waiters) w.reject(err);
    });
}

function queueLoad(key: string): Promise<unknown> {
  return new Promise((resolve, reject) => {
    if (!_pendingLoads) {
      _pendingLoads = new Map();
      // Microtask, not setTimeout: it still collapses everything issued in this tick, and
      // it does not add a macrotask of latency to a load that turns out to be alone.
      Promise.resolve().then(flushLoads);
    }
    const waiters = _pendingLoads.get(key);
    if (waiters) waiters.push({ resolve, reject });
    else _pendingLoads.set(key, [{ resolve, reject }]);
  });
}

// --- POST /api/events: fire-and-forget behavioral capture ------------------
//
// emitEvent() coalesces a tick's worth of events into one request, exactly like queueLoad
// above — a card list emitting an `impression` per visible row all lands in one POST. It is
// deliberately NOT routed through request(): capture must never trigger the 401 refresh flow,
// never flip the 402 paywall, and never throw. A dropped batch is a gap in an aggregate
// stream the matcher reads later, not a fact any single caller depends on.
let _pendingEvents: EventInput[] | null = null;

function flushEvents(): void {
  const batch = _pendingEvents;
  _pendingEvents = null;
  if (!batch || !batch.length) return;
  void rawFetch('/api/events', { method: 'POST', body: JSON.stringify({ events: batch }) })
    .catch(() => {
      /* telemetry: a failed send is a gap, never surfaced */
    });
}

function queueEvent(ev: EventInput): void {
  if (!_pendingEvents) {
    _pendingEvents = [];
    // Microtask flush, matching queueLoad: collapses this tick's events without adding a
    // macrotask of latency to one that turns out to be alone.
    Promise.resolve().then(flushEvents);
  }
  _pendingEvents.push(ev);
}

// The `exp` claim of a JWT, in ms, or null if it cannot be read. No verification — this
// only ever decides whether it is worth ASKING the server, which verifies for real.
function tokenExpiryMs(token: string): number | null {
  try {
    const payload = token.split('.')[1];
    if (!payload) return null;
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    const exp = (JSON.parse(json) as { exp?: number }).exp;
    return typeof exp === 'number' ? exp * 1000 : null;
  } catch {
    return null;
  }
}

// 30s of slack so a token about to expire mid-flight is treated as already expired.
function accessTokenLive(token: string): boolean {
  const exp = tokenExpiryMs(token);
  return exp !== null && exp - 30_000 > Date.now();
}

function sessionFromPayload(p: LoginResponse): SessionUser {
  return {
    userid: p.userid,
    firstName: p.firstName,
    lastName: p.lastName,
    email: p.email,
    location: p.location,
    subscription: p.subscription,
  };
}

async function applyTokens(p: LoginResponse): Promise<SessionUser> {
  _access = p.token;
  _refresh = p.refresh_token;
  _currentUser = sessionFromPayload(p);
  await Promise.all([
    saveTokens({ access: p.token, refresh: p.refresh_token }),
    saveSession(_currentUser),
  ]);
  // A refresh carries a freshly computed subscription block, so this is also how an
  // expiry that happened while the app was open reaches the UI.
  notifyUserChanged();
  return _currentUser;
}

async function forgetSession(): Promise<void> {
  _access = null;
  _refresh = null;
  _currentUser = null;
  _dataCache.clear();
  await Promise.all([clearTokens(), clearSession()]);
  for (const listener of _sessionLostListeners) listener();
}

// Carries the HTTP status alongside the message. Callers that only log or surface the text
// are unaffected (it is still an Error), but a caller that must ACT on the reason can now
// tell 404 "this opportunity is not in the catalog" from 402 "your trial lapsed" from a
// network failure. The Quest Log's refresh depends on that distinction: it used to collapse
// all three into null and report "no changes found" for opportunities it never checked.
export class HttpError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'HttpError';
    this.status = status;
  }
}

// Parse the server's `{"error": "..."}` body (Phase 2 error shape) for a useful message.
async function errorMessage(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { error?: string };
    if (body?.error) return body.error;
  } catch {
    /* non-JSON body */
  }
  return `API error ${res.status}`;
}

async function rawFetch(path: string, init?: RequestInit, withAuth = true): Promise<Response> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  if (withAuth && _access) headers.Authorization = `Bearer ${_access}`;
  return fetch(`${API_BASE}${path}`, { ...init, headers });
}

// One refresh attempt against POST /api/auth/refresh, shared across concurrent callers.
// Returns true if a fresh token pair is now in place.
function refreshOnce(): Promise<boolean> {
  if (_refreshInFlight) return _refreshInFlight;
  _refreshInFlight = (async () => {
    if (!_refresh) return false;
    try {
      const res = await rawFetch(
        '/api/auth/refresh',
        { method: 'POST', body: JSON.stringify({ refresh_token: _refresh }) },
        false,
      );
      if (!res.ok) return false; // expired/invalid/revoked refresh token
      await applyTokens((await res.json()) as LoginResponse);
      return true;
    } catch {
      return false;
    } finally {
      _refreshInFlight = null;
    }
  })();
  return _refreshInFlight;
}

// Authed request with the Phase 2 401 flow: on a 401, refresh once and retry; if refresh
// also fails, drop the session and throw AuthExpiredError so the router can bounce to login.
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res = await rawFetch(path, init);
  if (res.status === 401) {
    const refreshed = await refreshOnce();
    if (refreshed) {
      res = await rawFetch(path, init);
    }
    if (res.status === 401) {
      await forgetSession();
      throw new AuthExpiredError();
    }
  }
  // The subscription gate. Every 402 in this app means the same thing, so it is handled
  // here once: flip the cached identity to has_access:false (which routes the user to the
  // paywall) and still throw, so the caller's own error path is unchanged.
  if (res.status === 402) markSubscriptionBlocked();
  if (!res.ok) throw new HttpError(res.status, await errorMessage(res));
  return (await res.json()) as T;
}

// Salvaged from script.js: filter to text blocks, join, strip ```json fences, trim.
function cleanAiText(data: AiResponse): string {
  const textBlocks = (data.content ?? [])
    .filter((b) => b.type === 'text')
    .map((b) => b.text ?? '')
    .join('\n');
  const clean = textBlocks.replace(/```json|```/g, '').trim();
  if (!clean) throw new Error('Empty response from API');
  return clean;
}

export const httpClient: ApiClient = {
  // --- Auth ---
  // Startup used to be: load tokens -> POST /api/auth/refresh -> only then decide which
  // screen to render. That put a full server round trip in front of EVERY app open, before
  // the first screen had even started loading its own data, purely to recover the identity
  // payload — which we now keep beside the tokens.
  //
  // So: if the stored access token has not expired and we have a cached identity, boot from
  // it immediately and revalidate in the BACKGROUND. Nothing is trusted that wasn't before —
  // the token is still verified server-side on the very next call, and if the background
  // refresh comes back negative (revoked, token_version bumped, account gone) the session is
  // dropped and every onSessionLost listener fires, which bounces the user to /login.
  //
  // The slow path is unchanged and still awaited: no cached identity, or an access token
  // already past its `exp`, means we cannot know who this is without asking.
  async initAuth(): Promise<SessionUser | null> {
    const pair = await loadTokens();
    if (!pair) return null;
    _access = pair.access;
    _refresh = pair.refresh;

    const cached = await loadSession<SessionUser>();
    if (cached && accessTokenLive(pair.access)) {
      _currentUser = cached;
      // Fire-and-forget: a failure here tears the session down through forgetSession,
      // which notifies the listeners. Never awaited, or we are back where we started.
      void refreshOnce().then((ok) => {
        if (!ok) void forgetSession();
      });
      return _currentUser;
    }

    const ok = await refreshOnce();
    if (!ok) {
      await forgetSession();
      return null;
    }
    return _currentUser;
  },

  async login(userid: string, password: string): Promise<SessionUser> {
    const passwordHash = await sha256Hex(password);
    const res = await rawFetch(
      '/api/login',
      { method: 'POST', body: JSON.stringify({ userid, passwordHash }) },
      false,
    );
    if (!res.ok) throw new Error(await errorMessage(res));
    return applyTokens((await res.json()) as LoginResponse);
  },

  async register(input: RegisterInput): Promise<SessionUser> {
    const passwordHash = await sha256Hex(input.password);
    const body = {
      firstName: input.firstName,
      lastName: input.lastName,
      email: input.email,
      userid: input.userid,
      location: input.location,
      passwordHash,
      isAdult: input.isAdult,
      parentalConsent: input.parentalConsent,
      acceptedTerms: input.acceptedTerms,
    };
    const res = await rawFetch(
      '/api/register',
      { method: 'POST', body: JSON.stringify(body) },
      false,
    );
    if (!res.ok) throw new Error(await errorMessage(res));
    // Register auto-logs-in and returns the same payload as login.
    return applyTokens((await res.json()) as LoginResponse);
  },

  async logout(): Promise<void> {
    await forgetSession();
  },

  // --- Google sign-in ---
  googleStartUrl(appRedirect: string): string {
    return `${API_BASE}/api/auth/google/start?app_redirect=${encodeURIComponent(appRedirect)}`;
  },

  async googleSession(handoff: string): Promise<GoogleSessionResult> {
    const res = await rawFetch(
      `/api/auth/google/session?token=${encodeURIComponent(handoff)}`,
      { method: 'GET' },
      false,
    );
    if (!res.ok) throw new Error(await errorMessage(res));
    const data = (await res.json()) as LoginResponse & { pending?: boolean };
    if (data.pending) {
      return {
        status: 'pending',
        firstName: data.firstName,
        lastName: data.lastName,
        email: data.email,
      };
    }
    return { status: 'session', user: await applyTokens(data) };
  },

  async googleFinish(handoff: string, consent: GoogleFinishInput): Promise<SessionUser> {
    const res = await rawFetch(
      '/api/auth/google/finish',
      { method: 'POST', body: JSON.stringify({ token: handoff, ...consent }) },
      false,
    );
    if (!res.ok) throw new Error(await errorMessage(res));
    return applyTokens((await res.json()) as LoginResponse);
  },

  // --- Gated user data ---
  async loadData<T = unknown>(key: string): Promise<T | null> {
    return (await queueLoad(key)) as T | null;
  },

  // The last value seen for this key, or undefined if we have never loaded it. Synchronous
  // and never hits the network — a screen paints from this, then reconciles with loadData.
  peekData<T = unknown>(key: string): T | undefined {
    return _dataCache.has(key) ? (_dataCache.get(key) as T) : undefined;
  },

  async saveData(key: string, value: unknown): Promise<void> {
    await request<{ ok: boolean }>('/api/data/save', {
      method: 'POST',
      body: JSON.stringify({ key, value }),
    });
    // Write through only AFTER the server took it, so a failed save cannot seed the cache
    // with a value the account does not actually hold.
    _dataCache.set(key, value);
  },

  onSessionLost(listener: () => void): () => void {
    _sessionLostListeners.add(listener);
    return () => _sessionLostListeners.delete(listener);
  },

  onUserChanged(listener: (user: SessionUser | null) => void): () => void {
    _userChangedListeners.add(listener);
    return () => _userChangedListeners.delete(listener);
  },

  async saveLocation(location: string): Promise<void> {
    await request<{ ok: boolean }>('/api/account/location', {
      method: 'POST',
      body: JSON.stringify({ location }),
    });
  },

  // Resume upload is multipart — send FormData and let the browser set the boundary
  // (rawFetch's JSON Content-Type default would break the parse server-side).
  async extractFromResume(file: Blob, filename: string): Promise<string> {
    const form = new FormData();
    form.append('file', file, filename);
    let res = await fetch(`${API_BASE}/api/extract-from-resume`, {
      method: 'POST',
      headers: _access ? { Authorization: `Bearer ${_access}` } : undefined,
      body: form,
    });
    if (res.status === 401 && (await refreshOnce())) {
      res = await fetch(`${API_BASE}/api/extract-from-resume`, {
        method: 'POST',
        headers: _access ? { Authorization: `Bearer ${_access}` } : undefined,
        body: form,
      });
    }
    if (!res.ok) throw new Error(await errorMessage(res));
    const data = (await res.json()) as { extracted_text?: string };
    return data.extracted_text ?? '';
  },

  async extractFromLinkedIn(text: string): Promise<string> {
    const data = await request<{ extracted_text?: string }>('/api/extract-from-linkedin', {
      method: 'POST',
      body: JSON.stringify({ linkedin_text: text }),
    });
    return data.extracted_text ?? '';
  },

  // Still swallows every failure - the item is already in the student's Quest Log and a
  // review-queue miss must never surface as an error. But it is no longer fire-and-forget:
  // the resolved catalog id is the whole point of the call now (2026-08-24), because it is
  // what lets a hand-added opportunity share the catalog's deadline cache.
  async submitUserOpportunity(payload: UserOpportunitySubmission): Promise<string | null> {
    try {
      const res = await request<{ status?: string; id?: string | null }>(
        '/api/user-submitted-opportunities',
        { method: 'POST', body: JSON.stringify(payload) },
      );
      // The id is what lets a hand-added opportunity share the catalog's deadline cache.
      // Its absence is still not an error the student ever sees — they just get an item
      // that cannot be auto-checked, which the Quest Log now says plainly.
      return res?.id ?? null;
    } catch (e) {
      console.warn('Opportunity submission failed:', (e as Error).message);
      return null;
    }
  },

  // Subscription (payments deferred; these back the Manage Plan page's status + promo flow).
  // The freshest possible answer to "may this account use the app". It write-throughs to
  // the cached identity, which is what lifts the paywall the moment a promo code is
  // redeemed — without it the screen would say "beta access granted" while the router kept
  // bouncing the student back to it.
  async subscriptionStatus(): Promise<Record<string, unknown>> {
    const state = await request<Record<string, unknown>>('/api/subscription/status', { method: 'POST', body: '{}' });
    if (_currentUser && state && typeof state === 'object') {
      _currentUser = { ..._currentUser, subscription: state as SessionUser['subscription'] };
      void saveSession(_currentUser);
      notifyUserChanged();
    }
    return state;
  },
  async validatePromo(code: string): Promise<{ valid?: boolean; kind?: string; description?: string; error?: string }> {
    return request('/api/subscription/validate-promo', { method: 'POST', body: JSON.stringify({ promo_code: code }) });
  },
  async redeemPromo(code: string): Promise<Record<string, unknown>> {
    return request('/api/subscription/redeem-promo', { method: 'POST', body: JSON.stringify({ promo_code: code }) });
  },
  async subscriptionCheckout(promoCode: string): Promise<string | null> {
    const origin = (globalThis as { location?: { origin?: string } }).location?.origin ?? '';
    const data = await request<{ checkout_url?: string }>('/api/subscription/checkout', {
      method: 'POST',
      body: JSON.stringify({
        email: _currentUser?.email ?? '',
        promo_code: promoCode,
        success_url: origin ? `${origin}/subscription` : '',
        cancel_url: origin ? `${origin}/subscription` : '',
      }),
    });
    return data.checkout_url ?? null;
  },

  // --- Stable since Phase 1 (soft/public; bearer attached if present, for attribution) ---
  async getOpportunities(): Promise<Opportunity[]> {
    const data = await request<Opportunity[] | { opportunities?: Opportunity[] }>(
      '/api/opportunities',
    );
    return Array.isArray(data) ? data : (data.opportunities ?? []);
  },

  async getDeadlineCheck(oppId, force) {
    return (await this.getDeadlineCheckResult(oppId, force)).info;
  },

  // Same call, but says WHY it came back empty. `getDeadlineCheck` above stays the thin
  // never-throws wrapper for callers (the finder) that genuinely only want the overlay.
  // force=true appends ?refresh=1, which makes the server bypass its 7-day cache and run a
  // fresh paid check — the Quest Log's "Check for updates" button passes it; passive loads
  // do not, so an ordinary add/open still rides the free cross-user cache.
  async getDeadlineCheckResult(oppId, force) {
    try {
      const info = await request<Partial<TrackerInfo>>(
        `/api/opportunities/${encodeURIComponent(oppId)}/deadline${force ? '?refresh=1' : ''}`,
      );
      return { outcome: 'ok' as const, info };
    } catch (e) {
      const status = e instanceof HttpError ? e.status : 0;
      // 404 is not a failure — it is a tracked item with no catalog row behind it (an
      // opportunity the student added by URL before linking existed, or one whose
      // submission could not be resolved). It can never be auto-checked, which is a
      // different thing to say than "the check failed".
      const outcome =
        status === 404 ? ('not-found' as const)
        : status === 402 ? ('blocked' as const)
        : e instanceof AuthExpiredError || status === 401 ? ('auth' as const)
        : ('error' as const);
      return { outcome, info: null, message: (e as Error).message };
    }
  },

  // The shared, verified application checklist for one opportunity. Almost always a plain
  // read — generate_action_items.py has already written and verified the list onto the
  // catalog row — and it generates only for a row that agent has not reached yet.
  //
  // Never throws: a missing checklist must not stop an opportunity being tracked. The
  // caller falls back to the model's own (unverified, and therefore generic-only) items.
  async getActionItems(oppId) {
    try {
      return await request<ActionItemsResponse>(
        `/api/opportunities/${encodeURIComponent(oppId)}/action-items`,
      );
    } catch {
      return null;
    }
  },

  // FREE batch mirror of the catalog's cached deadline+task data for tracked ids. One GET,
  // no paid check, never throws — a failed sync leaves the snapshot untouched to retry later.
  async syncTracker(ids) {
    if (!ids.length) return {};
    try {
      const data = await request<{ items: Record<string, Partial<TrackerInfo>> }>(
        `/api/tracker/sync?ids=${encodeURIComponent(ids.join(','))}`,
      );
      return data?.items ?? {};
    } catch {
      return {};
    }
  },

  // `maxTokens` is optional and clamped server-side into [MESSAGES_MAX_TOKENS, ceiling], so
  // it can only ever RAISE a call's headroom. Sent by callers whose answer length scales
  // with their input — profile-tag extraction and enrichment both return one item per thing
  // the profile mentions, which the uniform default silently truncated.
  async callGemini(system, userContent, useWebSearch = false, maxTokens): Promise<string> {
    const body: Record<string, unknown> = { system, userContent, useWebSearch };
    if (maxTokens) body.maxTokens = maxTokens;
    const data = await request<AiResponse>('/api/messages', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    return cleanAiText(data);
  },

  async callClaude(system, userContent, useWebSearch = false, maxTokens): Promise<string> {
    return (await this.callClaudeDetailed(system, userContent, useWebSearch, maxTokens)).text;
  },

  async callClaudeDetailed(
    system,
    userContent,
    useWebSearch = false,
    maxTokens,
  ): Promise<AiResult> {
    const body: Record<string, unknown> = { system, userContent, useWebSearch };
    if (maxTokens) body.maxTokens = maxTokens;
    const data = await request<AiResponse>('/api/messages-claude', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    // Mock mode returns no stop_reason; a missing one reads as a clean finish.
    return { text: cleanAiText(data), truncated: data.stop_reason === 'max_tokens' };
  },

  // --- Behavioral event capture (P-A) ---
  emitEvent(action: WingmanEventAction, opportunityId?: string | null, context?: Record<string, unknown>): void {
    // No-op when signed out: an unidentified caller cannot be attributed, so the server
    // would only drop it — skip the round trip. (rawFetch attaches the bearer when present.)
    if (!_access) return;
    const ev: EventInput = { action };
    if (opportunityId) ev.opportunity_id = opportunityId;
    if (context && Object.keys(context).length) ev.context = context;
    queueEvent(ev);
  },

  // --- Google Calendar sync ---
  googleCalendarConnectUrl(appRedirect?: string): string | null {
    if (!_access) return null;
    const params = new URLSearchParams({ token: _access });
    if (appRedirect) params.set('app_redirect', appRedirect);
    return `${API_BASE}/api/auth/google/calendar/start?${params.toString()}`;
  },

  async syncCalendar(events, sweep = false): Promise<CalendarSyncResult> {
    // Deliberately NOT routed through request(): that throws on any non-2xx, and a 409
    // here means "calendar not connected yet", which the caller answers with a connect
    // prompt rather than an error message. The 401 refresh-once flow is reproduced.
    const init: RequestInit = {
      method: 'POST',
      body: JSON.stringify({ events, sweep }),
    };
    let res = await rawFetch('/api/calendar/sync', init);
    if (res.status === 401) {
      const refreshed = await refreshOnce();
      if (refreshed) res = await rawFetch('/api/calendar/sync', init);
      if (res.status === 401) {
        await forgetSession();
        throw new AuthExpiredError();
      }
    }
    if (res.status === 409) return { ok: false, notConnected: true };
    if (!res.ok) return { ok: false, error: await errorMessage(res) };
    const data = (await res.json()) as {
      results?: { id: string; status: string; googleEventId?: string }[];
      deleted?: number;
      deduped?: number;
      sweepErrors?: string[];
      calendarName?: string;
      calendarLink?: string;
    };
    return {
      ok: true,
      results: data.results ?? [],
      deleted: data.deleted ?? 0,
      deduped: data.deduped ?? 0,
      sweepErrors: data.sweepErrors ?? [],
      calendarName: data.calendarName ?? 'Highschool Wingman',
      calendarLink: data.calendarLink ?? '',
    };
  },
};

export function currentUser(): SessionUser | null {
  return _currentUser;
}
