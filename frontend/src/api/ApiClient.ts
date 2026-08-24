import type { TrackerInfo } from '@/lib/tracker';
import type {
  AiResult,
  GoogleFinishInput,
  GoogleSessionResult,
  Opportunity,
  RegisterInput,
  SessionUser,
} from './types';

// The single seam every screen and every model-calling salvage module depends on.
//
// Why an interface: Phase 3 is built in parallel with Phase 2 (the JWT auth layer).
// The stable Phase-1 surface (opportunities + AI proxy, both usable in the backend's
// mock mode) is implemented now; the auth-dependent methods (login/register, token
// storage, the Bearer interceptor, 401 handling) are stubbed here and filled in once
// the Phase 2 contract lands — WITHOUT touching any screen or salvage module.
export interface ApiClient {
  // --- Stable since Phase 1 (work today, incl. backend mock mode) ---
  getOpportunities(): Promise<Opportunity[]>;
  // On-demand, cross-user-cached deadline check. Never rejects (returns null on failure)
  // so a hiccup can't block loading the tracker. userid attribution becomes a token in
  // Phase 2, but the endpoint itself is not auth-gated.
  getDeadlineCheck(oppId: string): Promise<Partial<TrackerInfo> | null>;
  callGemini(system: string, userContent: string, useWebSearch?: boolean): Promise<string>;
  callClaude(
    system: string,
    userContent: string,
    useWebSearch?: boolean,
    maxTokens?: number,
  ): Promise<string>;
  callClaudeDetailed(
    system: string,
    userContent: string,
    useWebSearch?: boolean,
    maxTokens?: number,
  ): Promise<AiResult>;

  // --- Auth (Phase 2 contract) ---
  // Load persisted tokens into memory on app start; returns the current session if the
  // stored token pair is still usable (refreshing if the access token has expired).
  initAuth(): Promise<SessionUser | null>;
  // Client hashes the password (SHA-256) before calling — pass the raw password.
  login(userid: string, password: string): Promise<SessionUser>;
  register(input: RegisterInput): Promise<SessionUser>;
  logout(): Promise<void>;

  // --- Google sign-in (redirect flow) ---
  // The backend URL to open; pass the app's own redirect URI so the callback hands the
  // one-time token back to the app (web origin or native scheme), not the backend SPA.
  googleStartUrl(appRedirect: string): string;
  // Resolve the one-time handoff token: either a full session or a pending new account.
  googleSession(handoff: string): Promise<GoogleSessionResult>;
  // Complete a pending Google account with consent + location.
  googleFinish(handoff: string, consent: GoogleFinishInput): Promise<SessionUser>;

  // --- Gated user data (Bearer token; identity from token, body userid ignored) ---
  loadData<T = unknown>(key: string): Promise<T | null>;
  saveData(key: string, value: unknown): Promise<void>;
  // Update the account's location (POST /api/account/location, hard-gated).
  saveLocation(location: string): Promise<void>;
}

// Thrown when a request needs auth but the session is gone/unrecoverable (refresh failed).
// The router catches this to bounce the user to /login.
export class AuthExpiredError extends Error {
  constructor(message = 'Session expired') {
    super(message);
    this.name = 'AuthExpiredError';
  }
}
