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
  // Curated entry requirements, written by refresh_opportunities.py. Added to
  // OPPORTUNITIES_FIELDS on 2026-08-24 so the tracker's extraction prompt can see the one
  // column in the catalog that actually knows a program's prerequisites.
  eligibility?: string | null;
  [key: string]: unknown;
}

// --- Curated match (POST /api/match; OPPORTUNITY_MATCHING_PLAN.md Phase 3) ---
// One profile theme as the matcher embeds it (from the filterTags slot's enriched tags).
export interface MatchTheme {
  theme: string;
  intent?: string | null;
  next_steps?: string | null;
}
// The Phase-2 student blob the endpoint consumes, plus the Phase-4 funnel request controls.
export interface MatchStudentBlob {
  grade?: number | null;
  location?: { state?: string | null; city?: string | null };
  profile_themes?: MatchTheme[];
  highlight_projects?: string[];
  // Set when the student explicitly picked a passion/research project in setup — the funnel then
  // asks a project-goal question instead of the engagement question.
  project_focus?: boolean;
  funnel_answers?: Record<string, unknown>;
  // Experience-type picked in setup (raw catalog `type` strings, expanded from the friendly
  // labels client-side). A PRE-recall filter: recall keeps only these types before top-100.
  type_prefs?: string[];
  // Phase 4: set `funnel:true` to run the progressive funnel; `pool_ids` carries the
  // client-narrowed survivors to the next rung so recall/embedding never re-runs.
  funnel?: boolean;
  pool_ids?: string[] | null;
  // "Show my matches now": curate the current narrowed pool immediately instead of asking
  // another question. Rides on pool_ids — no recall re-run.
  curate_now?: boolean;
}
// One curated card the endpoint returns (display fields + the curation verdict).
export interface MatchResultCard {
  id: string;
  reason?: string | null;
  tier?: 'strong' | 'look' | null;
  exploration_pick?: boolean;
  [key: string]: unknown;
}
// One funnel option (Phase 4) with its live survivor count.
export interface MatchFunnelOption {
  label: string;
  value: string;
  count?: number | null;
}
// Per-candidate classification the server returns for a funnel rung, already quote-sanitized
// (a "cut" is safe to act on — the eligibility guard ran server-side). The client narrows by
// keeping every id whose disposition under the chosen option is not "cut".
export interface MatchFunnelClassificationEntry {
  per_option?: Record<string, string>;
  quote?: string | null;
  source_field?: string | null;
  quote_reverted?: boolean;
}
// The /api/match response is a union keyed on `done`:
//   done === false -> a funnel rung (axis/question/options/classification/pool_ids)
//   done !== false -> the curated shortlist (results); Phase-3 direct mode omits `done`.
export interface MatchResponse {
  done?: boolean;
  // curated-list shape
  results?: MatchResultCard[];
  pool_size?: number;
  rescued?: string[];
  guard_overrode_count?: number;
  note?: string | null;
  // funnel-rung shape (present when done === false)
  axis?: string;
  // "vibe" marks a rerank-only behavioral rung (no counts, no filtering); absent = a filter rung.
  kind?: string;
  // The rung offers a free-text "Something else" escape (the engagement filter) — its text
  // becomes a curation rerank preference and does not cut the pool.
  allow_other?: boolean;
  question?: string | null;
  rationale?: string | null;
  options?: MatchFunnelOption[];
  classification?: Record<string, MatchFunnelClassificationEntry>;
  pool_ids?: string[];
}
