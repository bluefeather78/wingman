import type { RawActionItem, TrackerInfo } from '@/lib/tracker';
import type {
  AiResult,
  GoogleFinishInput,
  GoogleSessionResult,
  MatchResponse,
  MatchStudentBlob,
  Opportunity,
  RegisterInput,
  SessionUser,
} from './types';

// Behavioral event capture (P-A) — the append-only telemetry the matcher's revealed-
// preference loop will read (saves up, dismisses down, "not interested" -> re-rank). The
// action set is kept in step with app.core._VALID_EVENT_ACTIONS, which the server whitelists;
// anything else is dropped server-side.
export type WingmanEventAction =
  | 'impression'  // the row was SHOWN (context: {rank, tier, kind, query}) — the weak denominator
  | 'open'        // opened the card / detail
  | 'save'        // saved for later
  | 'track'       // added to the Quest Log (a real commitment)
  | 'apply_click' // clicked through to apply / learn more (strongest positive)
  | 'dismiss'     // "not interested" (explicit negative; context: {reason})
  | 'untrack'     // removed from the Quest Log (explicit negative)
  | 'search'      // ran a search (context: {query})
  | 'tag_filter'; // toggled a profile-tag facet (context: {tag})

export interface EventInput {
  action: WingmanEventAction;
  // The opportunity this event is about; omit for search / tag_filter.
  opportunity_id?: string | null;
  // Per-action detail: {rank, tier, kind, query, reason, ...}.
  context?: Record<string, unknown>;
}

export interface ActionItemsResponse {
  action_items?: RawActionItem[];
  // How the list was arrived at — 'page-verified', 'page-empty', 'generic-fallback',
  // 'unparsed' or 'stored'. Mirrors opportunities.action_items_source.
  source?: string;
}

// The single seam every screen and every model-calling salvage module depends on.
//
// Why an interface: Phase 3 is built in parallel with Phase 2 (the JWT auth layer).
// The stable Phase-1 surface (opportunities + AI proxy, both usable in the backend's
// mock mode) is implemented now; the auth-dependent methods (login/register, token
// storage, the Bearer interceptor, 401 handling) are stubbed here and filled in once
// the Phase 2 contract lands — WITHOUT touching any screen or salvage module.
// What a deadline check actually did, for callers that report back to the student.
//   ok         a payload came back (it may still be a cached or fallback answer; the
//              payload's own `source` says which)
//   not-found  no catalog row behind this id — an item added by URL that never linked.
//              NOT a failure: it simply cannot be auto-checked.
//   blocked    402, the subscription gate refused a paid check
//   auth       the session expired
//   error      anything else: network, 5xx, malformed response
export type DeadlineCheckOutcome = 'ok' | 'not-found' | 'blocked' | 'auth' | 'error';

export interface DeadlineCheckResult {
  outcome: DeadlineCheckOutcome;
  info: Partial<TrackerInfo> | null;
  message?: string;
}

export interface ApiClient {
  // --- Stable since Phase 1 (work today, incl. backend mock mode) ---
  getOpportunities(): Promise<Opportunity[]>;
  // The curated match (OPPORTUNITY_MATCHING_PLAN.md Phase 3): server-side recall -> curation
  // over the whole catalog, returning <=10 curated cards for the student blob. Replaces the
  // client-side 7-kind fan-out on the "Suggest for me" path. Rejects on failure (the caller
  // surfaces it), unlike the never-throw catalog reads.
  match(body: MatchStudentBlob): Promise<MatchResponse>;
  // On-demand, cross-user-cached deadline check. Never rejects (returns null on failure)
  // so a hiccup can't block loading the tracker. Pass force=true to bypass the 7-day cache
  // and run a fresh paid check now — that is what the Quest Log's "Check for updates" button
  // does; passive loads omit it and ride the free cache.
  getDeadlineCheck(oppId: string, force?: boolean): Promise<Partial<TrackerInfo> | null>;
  // The same call with the REASON attached. Anything that reports back to the student
  // must use this one: a 404 (no catalog row behind this tracked item) and a 402 (trial
  // lapsed) and a network failure are three different things to tell somebody, and
  // collapsing them into a bare null is what made the Quest Log's refresh claim it had
  // checked opportunities it had not. force=true bypasses the 7-day cache (see above).
  getDeadlineCheckResult(oppId: string, force?: boolean): Promise<DeadlineCheckResult>;
  // The shared application checklist for one opportunity, generated and verified against
  // the program's own page by generate_action_items.py. Never rejects (null on failure):
  // a missing checklist must not stop an opportunity being tracked, and the caller falls
  // back to the model's own items, which are forced generic because nothing verified them.
  getActionItems(oppId: string): Promise<ActionItemsResponse | null>;
  // FREE, read-only batch mirror of the catalog's CURRENT cached deadline+task data for a set
  // of tracked ids, in one round trip (GET /api/tracker/sync). NEVER triggers a paid check —
  // it is the cheap "keep the snapshot in step with the catalog" half of the freshness model,
  // fired on app-open/login and screen focus. Returns {} on failure (never rejects): a sync
  // that cannot run must leave the snapshot exactly as it was, to retry later.
  syncTracker(ids: string[]): Promise<Record<string, Partial<TrackerInfo>>>;
  callGemini(system: string, userContent: string, useWebSearch?: boolean, maxTokens?: number): Promise<string>;
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
  // Last value seen for this key, synchronously, or undefined if never loaded. A render
  // accelerator only — it never replaces the loadData() that reconciles it.
  peekData<T = unknown>(key: string): T | undefined;
  saveData(key: string, value: unknown): Promise<void>;
  // Subscribe to "the session we booted from cache turned out to be dead". Returns an
  // unsubscribe. Fires whenever the session is dropped, including the background
  // revalidation initAuth kicks off.
  onSessionLost(listener: () => void): () => void;
  // Subscribe to "the identity we hold changed without a new login" — a background refresh
  // returning a fresh subscription block, a 402 telling us access has lapsed, or a
  // subscriptionStatus() read. Returns an unsubscribe.
  onUserChanged(listener: (user: SessionUser | null) => void): () => void;
  // Update the account's location (POST /api/account/location, hard-gated).
  saveLocation(location: string): Promise<void>;
  // Resume / LinkedIn quick-add extraction (both hard-gated; return the extracted text).
  extractFromResume(file: Blob, filename: string): Promise<string>;
  extractFromLinkedIn(text: string): Promise<string>;
  // Queue a user-submitted opportunity for the review queue (soft auth: provenance comes
  // from the token when signed in, unattributed otherwise). Never rejects — the row is a
  // background nicety, the student's own Quest Log already has the item.
  // Resolves to the catalog id the submission landed on (a fresh pending row, or an
  // existing row when the URL was already in the catalog), or null if it could not be
  // resolved. The Quest Log tracks the item under that id so it can use the same shared,
  // cached deadline check catalog opportunities use. Still never rejects.
  submitUserOpportunity(payload: UserOpportunitySubmission): Promise<string | null>;
  // Subscription status + promo flow (payments themselves stay deferred).
  subscriptionStatus(): Promise<Record<string, unknown>>;
  validatePromo(code: string): Promise<{ valid?: boolean; kind?: string; description?: string; error?: string }>;
  redeemPromo(code: string): Promise<Record<string, unknown>>;
  // Returns the Stripe checkout URL, or throws when payments aren't configured.
  subscriptionCheckout(promoCode: string): Promise<string | null>;

  // --- Behavioral event capture (P-A) ---
  // Fire-and-forget: record that the student did something. Batched per tick and POSTed to
  // /api/events. NEVER throws, never blocks the UI, and is a no-op when signed out — capture
  // is telemetry the matcher reads later, so a dropped event is a gap in an aggregate stream,
  // never an error the caller has to handle.
  emitEvent(action: WingmanEventAction, opportunityId?: string | null, context?: Record<string, unknown>): void;

  // --- Google Calendar sync ---
  // The backend URL to open to grant calendar access. This is a SEPARATE grant from
  // Google Sign-In (which only ever asks for openid/email/profile), so an account that
  // signed in with Google still has to go through it. A top-level navigation can't carry
  // an Authorization header, so the access token rides in the query string and the server
  // derives the userid from it.
  googleCalendarConnectUrl(appRedirect?: string): string | null;
  // Upsert the given deadline events into the user's dedicated Wingman calendar, and
  // (when sweep is set) delete the events we previously wrote for anything no longer in
  // the list. Returns the raw outcome rather than throwing on 409, because "calendar not
  // connected yet" is a prompt to connect, not an error.
  syncCalendar(
    events: CalendarSyncEvent[],
    sweep?: boolean,
  ): Promise<CalendarSyncResult>;
}

// The catalog's own column set — `apply_url`/`requirements` are carried in the
// submission payload, NOT as opportunities columns (see url_dedupe's note on that).
export interface UserOpportunitySubmission {
  name: string;
  url: string;
  type?: string;
  section?: string;
  meta?: string;
  fit?: string;
  note?: string;
  important_dates?: unknown[];
  requirements?: unknown[];
  apply_url?: string;
  category?: string | null;
}

export interface CalendarSyncEvent {
  // `${itemId}::${dateIdx}` — also the wingmanId stamped on the Google event.
  id: string;
  title: string;
  description: string;
  dateISO: string;
  googleEventId?: string | null;
}

export type CalendarSyncResult =
  | { ok: true; results: { id: string; status: string; googleEventId?: string }[];
      deleted: number;
      /** Duplicate events removed - two calendar entries the app had written for one date. */
      deduped: number;
      sweepErrors: string[];
      /** The dedicated calendar events land on - NOT the student's primary calendar. */
      calendarName: string;
      /** Opens Google Calendar focused on that calendar, when we wrote at least one event. */
      calendarLink: string }
  | { ok: false; notConnected: true }
  | { ok: false; notConnected?: false; error: string };

// Thrown when a request needs auth but the session is gone/unrecoverable (refresh failed).
// The router catches this to bounce the user to /login.
export class AuthExpiredError extends Error {
  constructor(message = 'Session expired') {
    super(message);
    this.name = 'AuthExpiredError';
  }
}
