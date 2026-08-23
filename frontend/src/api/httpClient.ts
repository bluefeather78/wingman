import { AuthExpiredError, type ApiClient } from './ApiClient';
import { sha256Hex } from './hash';
import { clearTokens, loadTokens, saveTokens } from './tokenStore';
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
// native builds need an absolute URL (EXPO_PUBLIC_API_BASE, e.g. the Render service URL).
const API_BASE = process.env.EXPO_PUBLIC_API_BASE ?? '';

// --- In-memory session state (persisted via tokenStore) ---------------------
let _access: string | null = null;
let _refresh: string | null = null;
let _currentUser: SessionUser | null = null;
// Shared so concurrent 401s trigger exactly one refresh, not one each.
let _refreshInFlight: Promise<boolean> | null = null;

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
  await saveTokens({ access: p.token, refresh: p.refresh_token });
  return _currentUser;
}

async function forgetSession(): Promise<void> {
  _access = null;
  _refresh = null;
  _currentUser = null;
  await clearTokens();
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
  if (!res.ok) throw new Error(await errorMessage(res));
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
  async initAuth(): Promise<SessionUser | null> {
    const pair = await loadTokens();
    if (!pair) return null;
    _access = pair.access;
    _refresh = pair.refresh;
    // We have no stored user profile, only tokens — refresh to (a) validate the session and
    // (b) recover the user payload. If it fails, the session is gone.
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
    const data = await request<{ value: T | null }>('/api/data/load', {
      method: 'POST',
      body: JSON.stringify({ key }),
    });
    return data.value ?? null;
  },

  async saveData(key: string, value: unknown): Promise<void> {
    await request<{ ok: boolean }>('/api/data/save', {
      method: 'POST',
      body: JSON.stringify({ key, value }),
    });
  },

  // --- Stable since Phase 1 (soft/public; bearer attached if present, for attribution) ---
  async getOpportunities(): Promise<Opportunity[]> {
    const data = await request<Opportunity[] | { opportunities?: Opportunity[] }>(
      '/api/opportunities',
    );
    return Array.isArray(data) ? data : (data.opportunities ?? []);
  },

  async getDeadlineCheck(oppId) {
    try {
      return await request(`/api/opportunities/${encodeURIComponent(oppId)}/deadline`);
    } catch {
      return null;
    }
  },

  async callGemini(system, userContent, useWebSearch = false): Promise<string> {
    const data = await request<AiResponse>('/api/messages', {
      method: 'POST',
      body: JSON.stringify({ system, userContent, useWebSearch }),
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
};

export function currentUser(): SessionUser | null {
  return _currentUser;
}
