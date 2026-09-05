import type { Opportunity } from '@/api/types';
import { callFeatureJSON, type FeatureCall } from './aiJson';
import type { Bucket } from './constants';

// Tracker model ported from script.js. The on-demand deadline-check FETCH
// (/api/opportunities/<id>/deadline) is auth-dependent and lives on the ApiClient;
// everything here is pure or model-backed-via-injection.

// Kind key -> tracker bucket.
export function findBucketForKind(kind: string): Bucket {
  const map: Record<string, Bucket> = {
    summer: 'summerPrograms',
    internship: 'internships',
    'research-competition': 'researchCompetitions',
    'pure-competition': 'pureCompetitions',
    conference: 'conferences',
    journal: 'journals',
    // The six buckets are fixed (ALL_BUCKETS) and there is no volunteering one, so a
    // volunteer role files under Internships — "a hands-on position with an organization"
    // is what it actually is. It used to hit the summerPrograms fallback below, which
    // labelled every volunteer role a summer camp in the Quest Log.
    volunteer: 'internships',
  };
  return map[kind] || 'summerPrograms';
}

// Catalog `type` -> kind key. Ported from finder.tsx so both Fresh Finds and the Quest
// Log's catalog search resolve a bucket the same way; pair with findBucketForKind above.
export function kindForOpp(opp: Opportunity): string {
  const map: Record<string, string> = {
    Program: 'summer', Internship: 'internship', Conference: 'conference',
    Journal: 'journal', Research: 'research-competition', Competition: 'pure-competition',
    Volunteer: 'volunteer', Academic: 'pure-competition',
  };
  return map[(opp.type as string) ?? ''] ?? 'summer';
}

export function todayLabel(): string {
  return new Date().toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
}

export function baseDomain(url: string): string {
  try {
    const u = new URL(url);
    return `${u.protocol}//${u.hostname}`;
  } catch {
    return url;
  }
}

export interface ImportantDate {
  label: string;
  date_iso: string;
  type: 'opens' | 'deadline' | 'event_start' | 'event_end' | 'other';
  // Per-DATE, not per-row. A row routinely mixes a confirmed deadline with a projected
  // opening, and `was_estimated` (row-level) cannot express that — it only says "something
  // here is a guess". Absent on every row written before 2026-08-24; treated as unknown
  // rather than false, so an old row is never labelled confirmed on no evidence.
  estimated?: boolean;
  // P6c date verification: whether this date was found (date-aware match) in the content of
  // a page the deadline check actually FETCHED, and if so which page. An estimated/projected
  // date is verified:false by design — it is a prediction, not on any page. Absent on rows
  // written before 2026-08-26: unknown, never rendered as verified.
  verified?: boolean;
  source_url?: string;
}

export interface TrackerInfo {
  status: 'running' | 'not_running' | 'rolling' | 'unknown';
  meta?: string;
  fit?: string;
  note?: string;
  noteType?: 'good' | 'plain' | 'flag';
  important_dates?: ImportantDate[];
  deadline_label?: string;
  was_estimated?: boolean;
  requirements?: { date: string; text: string }[];
  apply_url?: string;
  apply_label?: string;
  calendar_events?: { date: string; text: string; type: string }[];
  // basis/evidence: see ActionItem in api/trackerStore.ts. A model that omits them is
  // treated as having said 'generic' — the absence of a claim of proof is not proof.
  // source_tier/source_url/source_domain (P6b): the trust tier of the SOURCE whose page the
  // task's quote was verified against, and which page that was. The serve path already
  // withholds pending/blocked tiers, so a client only ever sees official/trusted here.
  action_items?: {
    text: string; url: string | null; basis?: string; evidence?: string | null;
    source_tier?: string; source_url?: string | null; source_domain?: string | null;
  }[];
  important_date_note?: string;
  // Only ever set by GET /api/opportunities/<id>/deadline — how trustworthy this payload
  // is. See VERIFIED_DEADLINE_SOURCES below; never present on an extractTrackerInfo result.
  source?: string;
  // When the CATALOG last verified this row's deadlines (opportunities.dates_last_checked_at).
  // Returned by the deadline endpoint and the batch /api/tracker/sync. The free sync uses the
  // freshest of these across tracked items to stamp the Quest Log's "Last checked" line — so
  // the line reflects when the DATA was actually verified, not when the mirror ran.
  dates_last_checked_at?: string | null;
}

// Which `source` values on a deadline payload represent an answer somebody actually
// verified. Mirrors check_deadlines.SOURCE_* and the endpoint's own flags:
//   'cached'             a previously verified answer, still inside the 7-day TTL
//   'fresh, real search' just verified by a live search
// Everything else is an echo or a fabrication and must never DELETE anything:
//   'mock'                no API key; the dates are invented for local testing
//   'unverified-fallback' the model answered without searching
//   'unparsed-fallback'   it searched, but the extraction was unreadable
//   'kept-existing'       it searched and found nothing, so the row kept its dates
//   'stale-fallback'      the check raised; this is whatever was cached, however old
const VERIFIED_DEADLINE_SOURCES = ['cached', 'fresh, real search'];

export function isVerifiedDeadlineSource(source: string | null | undefined): boolean {
  return !!source && VERIFIED_DEADLINE_SOURCES.includes(source);
}

// The shared/cached deadline check is authoritative for status/important_dates/was_estimated
// when present (it's verified server-side and shared across every user tracking the same
// opportunity, unlike extractTrackerInfo's own per-call guess). Overlays in place.
//
// An EMPTY important_dates only overwrites when the source is verified. The guard used to be
// a bare `.length`, which meant a verified "this program is discontinued, no dates" could
// never clear the dates extractTrackerInfo had guessed — the card ended up showing
// status=unknown next to confident-looking dates nothing had ever confirmed. Widening it to
// clear on ANY empty payload would be worse: a mock or fallback response echoes an empty
// array too, and would wipe good data.
export function applyDeadlineCheckToInfo(
  info: TrackerInfo,
  deadlineInfo: Partial<TrackerInfo> | null | undefined,
): void {
  if (!deadlineInfo) return;
  const verified = isVerifiedDeadlineSource(deadlineInfo.source);
  if (
    deadlineInfo.status &&
    ['running', 'not_running', 'rolling', 'unknown'].includes(deadlineInfo.status)
  ) {
    info.status = deadlineInfo.status;
  }
  if (Array.isArray(deadlineInfo.important_dates)
      && (verified || deadlineInfo.important_dates.length)) {
    info.important_dates = deadlineInfo.important_dates;
  }
  if (typeof deadlineInfo.was_estimated === 'boolean') info.was_estimated = deadlineInfo.was_estimated;
  if (deadlineInfo.important_date_note) info.note = deadlineInfo.important_date_note;
}

// ---------- Action items: the client no longer GENERATES any (P8) ----------
//
// Until P8 both client prompts asked the model for action items, under a carefully-worded
// rules block descended from the fabricated-"Algebra 2" incident. That block is gone with
// the prompts that used it, deliberately: tasks now come ONLY from
// GET /api/opportunities/<id>/action-items — the one place a task's quote has been checked
// in code against page content we actually fetched — or, for a tracked item with no catalog
// row behind it, from the static generic checklist below, which asserts nothing and so has
// nothing to get wrong. A model asked for tasks in the browser has never seen the page and
// cannot be verified; removing the ask removes the failure mode rather than fencing it.

export type RawActionItem = NonNullable<TrackerInfo['action_items']>[number];

export interface NormalizedActionItem {
  id: string;
  text: string;
  url: string | null;
  state: string;
  basis: 'page' | 'generic';
  evidence: string | null;
  // The trust tier of the source that carried this task's verified quote (P7 renders it as
  // the source chip). Null on generic tasks. 'official' = the program's own page/PDF;
  // 'trusted' = an operator-approved aggregator domain.
  sourceTier: 'official' | 'trusted' | null;
  // The EVIDENCE link — the specific fetched page the quote was verified against. Distinct
  // from `url` above, which is the step's ACTION link (where to go do the task).
  sourceUrl: string | null;
  sourceDomain: string | null;
}

// Tasks from GET /api/opportunities/<id>/action-items. `basis` is honoured here and ONLY
// here, because that endpoint is the only place a task's quote has been checked against
// page text we fetched ourselves (page_text.quote_is_on_page / claim_is_supported). A claim
// is page-backed because a page backed it, never because a model said so — since P8 no
// client code path even asks a model for tasks, so this normalizer and the static checklist
// below are the only two ways a task can enter the tracker.
export function normalizeVerifiedActionItems(
  raw: RawActionItem[] | undefined,
  idPrefix: string,
): NormalizedActionItem[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((ai) => ai && typeof ai.text === 'string' && ai.text.trim())
    .slice(0, 5)
    .map((ai, i) => {
      const evidence = typeof ai.evidence === 'string' && ai.evidence.trim()
        ? ai.evidence.trim()
        : null;
      // Defense in depth, mirroring the serve path's _servable(): a pending/blocked tier
      // must never render as page-backed even if a bug upstream lets one through.
      const withheldTier = ai.source_tier === 'pending' || ai.source_tier === 'blocked';
      const pageBacked = ai.basis === 'page' && !!evidence && !withheldTier;
      // Legacy page-backed items (pre-P6b, no source_tier) read as OFFICIAL, not unknown:
      // the urllib pipeline that wrote them only ever fetched the program's own page, so
      // their provenance is known — the field, not the proof, is what arrived later.
      const sourceTier = pageBacked
        ? ((ai.source_tier === 'trusted' ? 'trusted' : 'official') as 'official' | 'trusted')
        : null;
      return {
        id: `${idPrefix}-t${i}`,
        text: ai.text.trim(),
        url: typeof ai.url === 'string' && ai.url.startsWith('http') ? ai.url : null,
        state: 'not_started',
        basis: (pageBacked ? 'page' : 'generic') as 'page' | 'generic',
        evidence: pageBacked ? evidence : null,
        sourceTier,
        sourceUrl: pageBacked && typeof ai.source_url === 'string' && ai.source_url.startsWith('http')
          ? ai.source_url
          : null,
        sourceDomain: pageBacked && typeof ai.source_domain === 'string' && ai.source_domain.trim()
          ? ai.source_domain.trim()
          : null,
      };
    });
}

// The client-side twin of generate_action_items.py's GENERIC_DEFAULT, for the one case the
// verified endpoint cannot cover: a tracked item with no catalog row behind it (a user
// submission that resolved to id:null). Every line must assert NOTHING program-specific —
// the same bar the server's generic checklists are tested against — so there is nothing to
// verify and nothing to get wrong.
const STATIC_GENERIC_TASKS = [
  'Read the eligibility and application page',
  'Note the application deadline',
  'Check what must be submitted, and in what format',
  'Ask a teacher or mentor for a recommendation',
];

export function staticGenericChecklist(idPrefix: string, url?: string | null): NormalizedActionItem[] {
  return STATIC_GENERIC_TASKS.map((text, i) => ({
    id: `${idPrefix}-t${i}`,
    text,
    url: url ?? null,
    state: 'not_started',
    basis: 'generic' as const,
    evidence: null,
    sourceTier: null,
    sourceUrl: null,
    sourceDomain: null,
  }));
}

// P8: SLIMMED to the two descriptive fields nothing else produces. This call used to
// re-derive dates, status, tasks and an apply URL that the two Claude endpoints (deadline /
// action-items) already produce verified — a redundant producer whose unverified guesses
// could sit beside (or wipe) the verified answers. Now: dates/status come only from the
// deadline endpoint, tasks only from the action-items endpoint, the apply link is the
// catalog's own link-checked opp.url, and this asks for meta/fit alone.
//
// Web search is OFF, deliberately. meta/fit are descriptive — what the program is, from the
// catalog's own summary/eligibility/price/location — and a search-ON prompt demanding dates
// it can no longer emit is the exact "search theater" failure this repo documents. With no
// dates asked for, there is nothing here that needs a source.
//
// The prompt itself is now app/services/prompts.py's `tracker_extract` (S1-1). Its opening
// line is still what generate_mock_text matches on, so rewording it there blanks mock mode
// — but it no longer decides cost attribution, which reads the feature id. (The mock returns
// the old full shape; the extra keys are simply ignored here.)
export interface TrackerMetaFit {
  meta?: string;
  fit?: string;
}

export async function extractTrackerInfo(
  callFeature: FeatureCall,
  opp: Opportunity,
): Promise<TrackerMetaFit> {
  return callFeatureJSON<TrackerMetaFit>(callFeature, 'tracker_extract', { opp });
}

// intakeExtractAndClassify and its IntakeInfo type were DELETED by S1-1 (finding C1.2), not
// ported. It had no caller anywhere in the app — and of all the prompts in the bundle it
// advertised the exploit shape most loudly: the ONLY `useWebSearch: true` in the frontend,
// wrapped around an elaborate multi-step search plan. Search is pinned off server-side
// (S0-3) so it was already harmless, but a worked example of "here is how to make this app
// run paid searches on your behalf" does not belong in a file anyone can read.

// Stable, collision-free id within the target bucket — slugifyTracker(), ported.
export function slugifyTracker(text: string, usedIds: Iterable<string>): string {
  let base = (text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '').slice(0, 50);
  if (!base) base = 'opportunity';
  const used = new Set(usedIds);
  let id = base;
  let n = 2;
  while (used.has(id)) {
    id = `${base}-${n}`;
    n += 1;
  }
  return id;
}
