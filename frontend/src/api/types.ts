// Shared API types. The AI-call request/response shape is the backend-agnostic
// contract from Phase 1 (server.py's /api/messages and /api/messages-claude): the
// client sends {system, userContent, useWebSearch} and the server normalizes both
// live and mock responses into a {content:[{type,text}], stop_reason?} envelope.
// This shape is STABLE across the Phase 2 auth work — only how the caller is
// identified changes (a Bearer token instead of an inline `userid`).

export interface AiRequest {
  system: string;
  userContent: string;
  useWebSearch?: boolean;
  maxTokens?: number;
}

export interface AiTextBlock {
  type: string;
  text?: string;
}

export interface AiResponse {
  content?: AiTextBlock[];
  stop_reason?: string;
}

export interface AiResult {
  text: string;
  truncated: boolean;
}

// The subscription block from subscription_state() — carried on every login payload.
export interface SubscriptionState {
  status?: string; // trial | beta | active | canceled | past_due | ...
  days_left?: number;
  has_access?: boolean;
  [key: string]: unknown;
}

// The login/register/refresh response payload (Phase 2 contract: `login_response`).
export interface SessionUser {
  userid: string;
  firstName?: string;
  lastName?: string;
  email?: string;
  location?: string;
  subscription?: SubscriptionState;
}

export interface LoginResponse extends SessionUser {
  ok: boolean;
  token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

// Registration input from the form. `password` is raw here — the client SHA-256s it into
// the `passwordHash` the Phase 2 contract expects, so hashing stays in one place.
export interface RegisterInput {
  firstName: string;
  lastName: string;
  email: string;
  userid: string;
  location: string;
  password: string;
  isAdult: boolean;
  parentalConsent: boolean;
  acceptedTerms: boolean;
}

// Google sign-in. The redirect flow hands back a one-time `handoff` token; /session either
// resolves it to a full session (existing/linked account) or reports `pending` (new account),
// in which case the app collects consent + location and calls /finish.
export type GoogleSessionResult =
  | { status: 'session'; user: SessionUser }
  | { status: 'pending'; firstName?: string; lastName?: string; email?: string };

export interface GoogleFinishInput {
  location: string;
  isAdult: boolean;
  parentalConsent: boolean;
  acceptedTerms: boolean;
}

// The opportunity catalog row shape (subset used by the client). Source of truth is
// the Supabase `opportunities` table, proxied by GET /api/opportunities.
export interface Opportunity {
  id: string;
  name: string;
  org?: string | null;
  type?: string | null;
  url?: string | null;
  summary?: string | null;
  subject_tags?: string[] | null;
  grade_min?: number | null;
  grade_max?: number | null;
  [key: string]: unknown;
}
