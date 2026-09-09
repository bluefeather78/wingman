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
  // Two-tier model: a successful /api/ai call echoes the caller's Free-tier allowance so the
  // client meter can tick without a second request. Absent for Paid (unlimited) and mock.
  meta?: { allowance?: AllowanceSnapshot };
}

// The Free-tier daily AI allowance snapshot (TWO_TIER_AI_PLAN.md §4.2). Carried on a 429
// (cap hit) and echoed on a successful /api/ai call. `unlimited` marks the Paid tier, where
// the numeric fields are null.
export interface AllowanceSnapshot {
  tier?: 'free' | 'paid' | string;
  unlimited?: boolean;
  used?: number | null;
  limit?: number | null;
  remaining?: number | null;
  reset_at?: string | null;
}

export interface AiResult {
  text: string;
  truncated: boolean;
}

// The subscription block from subscription_state() — carried on every login payload.
export interface SubscriptionState {
  status?: string; // free | beta | active | canceled | past_due | (legacy) trial
  days_left?: number;
  // When a paid plan ends. For a `canceled` account still inside its paid period this is the
  // date it reverts to Free (cancel-at-period-end); the home page counts down to it.
  subscription_end_at?: string | null;
  has_access?: boolean;
  // Two-tier model: `in_paid_period` is true for a live paid/comped subscription and drives
  // the AI tier. `ai_tier` is the derived label the UI shows. has_access is always true now
  // (no lockout); the tier is the axis that varies.
  in_paid_period?: boolean;
  ai_tier?: 'free' | 'paid' | string;
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
  password: string;
  isAdult: boolean;
  parentalConsent: boolean;
  acceptedTerms: boolean;
}

// Google sign-in. The redirect flow hands back a one-time `handoff` token; /session either
// resolves it to a full session (existing/linked account) or reports `pending` (new account),
// in which case the app collects consent and calls /finish.
export type GoogleSessionResult =
  | { status: 'session'; user: SessionUser }
  | { status: 'pending'; firstName?: string; lastName?: string; email?: string };

export interface GoogleFinishInput {
  isAdult: boolean;
  parentalConsent: boolean;
  acceptedTerms: boolean;
}

// Sign in with Apple (native iOS, App Store 4.8). Unlike Google's redirect, the signed identity
// token is returned on-device; the app POSTs it to /api/auth/apple/native, which either resolves
// a full session (existing/linked account) or reports `pending` (new account), in which case the
// app collects consent and re-POSTs the SAME token with the consent booleans (GoogleFinishInput).
export interface AppleCredential {
  identityToken: string;
  // Apple returns the name only on the FIRST authorization, so both are optional.
  firstName?: string;
  lastName?: string;
}

// Same shape as GoogleSessionResult — session-or-pending — named apart for the Apple call site.
export type AppleSessionResult = GoogleSessionResult;

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

// POST /api/match/eligibility — gate an already-chosen candidate set (the finder's form/quiz
// path, which never touches /api/match and so was never eligibility-gated server-side).
export interface MatchEligibilityRequest {
  candidate_ids: string[];
  grade?: number | null;
  location?: { state?: string };
}

export interface MatchEligibilityResponse {
  excluded_ineligible: string[];
  checked: number;
  called: boolean;
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
