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

// --- POST /api/match (semantic recall + eligibility) -----------------------
// The trimmed recall endpoint the Fresh Finds "suggest" path posts to. It embeds the
// student's selected profile themes (+ any highlight projects), recalls the top rows by
// cosine, drops verified-ineligible ones, and returns the whole scored pool for the client
// grid to filter. Contract mirrors app/routes/matching.py.
export interface MatchThemeInput {
  theme: string;
  intent?: string | null;
  next_steps?: string | null;
}

export interface MatchRequest {
  grade?: number | null;
  location?: { state?: string };
  // Either bare theme strings or the richer {theme,intent,next_steps} shape — the server
  // accepts both. Fresh Finds sends the rich shape built from the student's filterTags.
  profile_themes: (string | MatchThemeInput)[];
  highlight_projects: string[];
}

// Each result IS a flattened Opportunity row plus its cosine `score` and a `strong` badge
// flag (score >= the server's fixed cut). Extends Opportunity so it drops straight into the
// finder's grid as `opp`.
export interface MatchResultRow extends Opportunity {
  score: number | null;
  strong: boolean;
}

export interface MatchResponse {
  results: MatchResultRow[];
  pool_size: number;
  excluded_ineligible: string[];
  embed_cost_usd: number;
  checked: number;
  note?: string | null;
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
  // Curated entry requirements, written by refresh_opportunities.py. Added to
  // OPPORTUNITIES_FIELDS on 2026-08-24 so the tracker's extraction prompt can see the one
  // column in the catalog that actually knows a program's prerequisites.
  eligibility?: string | null;
  [key: string]: unknown;
}
