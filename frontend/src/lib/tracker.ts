import type { Opportunity } from '@/api/types';
import { callGeminiJSON, type GeminiCall } from './aiJson';
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
// The opening line is the `tracker_extract` signature in _FEATURE_SIGNATURES AND the mock
// branch's match in generate_mock_text — reword it and cost attribution files this under
// `other` while mock mode goes blank. (The mock returns the old full shape; the extra keys
// are simply ignored here.)
export interface TrackerMetaFit {
  meta?: string;
  fit?: string;
}

export async function extractTrackerInfo(
  callGemini: GeminiCall,
  opp: Opportunity,
): Promise<TrackerMetaFit> {
  const system = `You extract structured tracking data for an extracurricular opportunity (program, internship, competition, or research position), for a high-school student's tracker.

You have NO web access in this call, and only two DESCRIPTIVE fields are wanted. Dates, deadlines, status and application tasks are researched and verified separately — NEVER include, estimate or mention any date, deadline, or application requirement here.

From the catalog details given, write:
- "meta": one short line of practical facts — location / cost / format / organizer — separated by ' · '. Use only facts stated in the given details; omit anything unknown rather than guessing. No dates.
- "fit": one sentence, under 25 words, on what this actually involves for a student.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON: {"meta":"...","fit":"..."}.`;
  const userContent = `Opportunity: ${opp.name} (${opp.org ?? ''})
URL: ${opp.url ?? ''}
Known info: ${opp.summary ?? ''}
${opp.eligibility ? `Eligibility (from catalog): ${opp.eligibility}\n` : ''}${opp.price ? `Cost (from catalog): ${opp.price}\n` : ''}${opp.location ? `Location (from catalog): ${opp.location}\n` : ''}
Write the two descriptive fields per the schema, from these details only.`;
  return callGeminiJSON<TrackerMetaFit>(callGemini, system, userContent, false);
}
// ---------- Intake: add a custom opportunity from a URL, on the Quest Log ----------
// Ported from the retired SPA's trackerIntakeExtractAndClassify(): the same prompt, and the
// same extra job — it must also CLASSIFY (pick a bucket), which extractTrackerInfo above
// never has to do because the finder already knows the kind.
export interface IntakeInfo extends TrackerInfo {
  name?: string;
  section?: string;
  category?: string | null;
}

export async function intakeExtractAndClassify(
  callGemini: GeminiCall,
  url: string,
  notes: string,
): Promise<IntakeInfo> {
  const today = todayLabel();
  const root = baseDomain(url);
  const thisYear = new Date().getFullYear();
  const nextYear = thisYear + 1;
  const system = `You classify and extract structured tracking data for a student extracurricular opportunity from a URL, for a high-school tracker. Today's date is ${today}.

First determine 'section': 'conferences' for academic conferences/workshops that review and present papers, 'journals' for academic/student journals with manuscript submission, 'researchCompetitions' for science fairs, app challenges, and project/research-based contests where a project or paper is submitted and judged, 'pureCompetitions' for skills/knowledge tests with no project submitted (olympiads, quiz competitions, exams), 'internships' for hands-on mentored work positions with a lab, company, or organization, 'summerPrograms' for camps, enrichment programs, or coursework.

Search thoroughly with web_search, in order: (1) the given URL; (2) "site:${root} ${nextYear}" / "site:${root} ${thisYear}" for a current/upcoming-cycle page (orgs often publish a year-specific page separate from the evergreen landing page); (3) ALWAYS ALSO search the most recent PAST cycle (e.g. "site:${root} ${thisYear} deadline" and the year before that, computed from ${today}) even if step 2 succeeded — this is your mandatory estimation basis; (4) "site:${root} FAQ"/"key dates"/"timeline" for the base site if still stale or missing. Look for language indicating the program is discontinued/not running this cycle — set status to "not_running" if so, and don't estimate dates for it.

Estimation is expected and encouraged, not a last resort — apply in order: (a) explicit current/upcoming-cycle dates found → use them, was_estimated:false; (b) no current-cycle dates but real prior-cycle dates found and the program looks recurring → roll each forward ~1 year, was_estimated:true, status:"running" (the expected path when a new cycle's page isn't live yet — don't default to "unknown"); (b2) the current cycle posts some real dates (usually just the deadline) but NOT the opening, and a prior cycle posted both → apply the prior cycle's opens-to-deadline INTERVAL to this cycle's real deadline rather than rolling last year's opening forward, was_estimated:true — when a cycle shifts the interval survives and the calendar date does not; (c) only a vague pattern or date window found (e.g. "opens in fall", "deadlines typically October-December") → this app exists so a student never misses a deadline, so an empty field is the worst outcome: you MUST construct a concrete estimated important_dates entry from it (never leave it as prose in note), was_estimated:true, explaining the real window briefly in note. Pick one representative day inside the window — the EARLIEST day for a "deadline" (bias early: a date shown before the true one is fail-safe, a date shown after it is the miss we exist to prevent), the FIRST day for "opens"/"event_start"/a bare season — keeping it on or after today and inside the window the note supports (the near edge, never a date conjured months before the range), and split a multi-part cycle into one entry each — and if a window's early edge is already past but it still extends beyond today, use the earliest day still on or after today rather than rolling it a full year forward (which hides a deadline still imminent this cycle); (d) genuinely nothing current or prior-cycle found after trying step 3 → status:"unknown" (should be rare).

Find EVERY pertinent date — registration opens, early-bird vs. regular deadline, notification date, and event/conference start-end dates — each with a short label and a "type" of "opens", "deadline", "event_start", "event_end", or "other", in chronological order. Pay particular, deliberate attention to the registration/application OPENS date, not just the deadline — this is the field most often missed. An "opens" entry is REQUIRED whenever there is an application or registration step: the tracker marks a program HAPPENING NOW once its first date has passed, so a program carrying only a deadline reads as "not started yet" until the day it closes. Project the prior cycle's opening date if the current one isn't posted (was_estimated:true), and only omit it if no cycle published one — saying why in "note" if so. Only omit a date category if there's genuinely no basis to find or estimate one - i.e. nothing current AND nothing from any prior cycle. Set "estimated" PER DATE: true if that date came from a prior cycle, an interval or a vague pattern, false only if explicitly posted for the current cycle — the tracker renders this next to each date, and do not also put "(estimated)" in the label. Every date you have enough basis to mention in "note" (e.g. "registration typically opens Sept") must ALSO appear as a matching "important_dates" entry (was_estimated:true) — never describe date info in "note" without a corresponding structured entry, and vice versa. Prefer including a reasonably-estimated date over omitting it.

Do NOT propose application tasks or action items — the application checklist is generated and verified separately, against the program's own page.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON: {"name":"program/opportunity name from the page, or organization name if no program name found, under 50 chars","section":"conferences, journals, researchCompetitions, pureCompetitions, internships, or summerPrograms","status":"running, not_running, or unknown","meta":"one short line: dates/location/fee/format","fit":"one sentence, under 25 words","note":"one sentence, under 25 words","noteType":"good, plain, or flag","important_dates":[{"label":"short label","date_iso":"YYYY-MM-DD","type":"opens, deadline, event_start, event_end, or other","estimated":true or false}],"deadline_label":"short text like ROLLING, only if important_dates is empty","was_estimated":true or false,"requirements":[{"date":"...","text":"under 12 words"}],"apply_url":"...","apply_label":"short button label","category":"short type label like 'Science fair' or 'Rationality camp', or null"}. Stay well within 1000 tokens: at most 4 important_dates and 3 requirements.`;
  const userContent = `URL: ${url}
${notes ? `Extra context: ${notes}
` : ''}
Fetch this URL, classify it, and extract tracking details per the schema.`;
  return callGeminiJSON<IntakeInfo>(callGemini, system, userContent, true);
}

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
