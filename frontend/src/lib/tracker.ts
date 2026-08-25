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
}

export interface TrackerInfo {
  status: 'running' | 'not_running' | 'unknown';
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
  action_items?: { text: string; url: string | null; basis?: string; evidence?: string | null }[];
  important_date_note?: string;
  // Only ever set by GET /api/opportunities/<id>/deadline — how trustworthy this payload
  // is. See VERIFIED_DEADLINE_SOURCES below; never present on an extractTrackerInfo result.
  source?: string;
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
    ['running', 'not_running', 'unknown'].includes(deadlineInfo.status)
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

// ---------- Action-item rules, shared by BOTH prompts so they cannot drift ----------
//
// This block replaced an instruction that read: "Infer these from the requirements you find
// AND FROM WHAT'S TYPICAL FOR THIS TYPE OF OPPORTUNITY." That is a licence to invent, and it
// was taken: a student tracking NYU's User Experience Design summer program was handed
// "Review prerequisite requirements (Algebra 2)" — a prerequisite that appears nowhere on
// the program's page and nowhere in its catalog row. "Algebra 2" is simply what a STEM
// summer program typically requires, which is exactly what the instruction asked for.
//
// The dates in this same response have carried a never-invent rule, a SELF-CHECK block and a
// server-side write guard for months. The tasks had none, and they render as flat
// authoritative text with no equivalent of the "(est.)" marker — so a student cannot tell an
// invented prerequisite from a real one. The harm is not a wasted afternoon: a fabricated
// eligibility bar makes a student self-reject from a program they actually qualify for.
//
// Note the prompt-level rule is only half the fix and is the weaker half. The other half is
// server-side verification of `evidence` against the page text we fetched ourselves — a
// prompt is guidance, code is a guarantee. See app/services/action_items.py.
const ACTION_ITEM_RULES = `Action items — the concrete things a student must DO to apply in time, e.g. request a recommendation letter, draft an essay, gather transcripts, prepare a portfolio, get parent/guardian sign-off, register for a required test, pay a fee. List 3-5, each under 10 words. Skip entirely if status is not_running.

EVERY action item is one of exactly two kinds, and you must label which:

- "basis":"page" — the item states something SPECIFIC about THIS program that you actually read on a page you retrieved. Set "evidence" to the exact sentence or phrase from that page, copied VERBATIM, that says so. Copy it character for character; do not paraphrase, tidy, translate or summarise it. This is checked against the real page text and an item whose quote is not found there is discarded.
- "basis":"generic" — ordinary application logistics that would be true of almost any program of this kind. Set "evidence" to null. A generic item must assert NOTHING specific about this program: "Draft your personal statement" is generic, "Draft the 500-word statement on your research goals" is not.

NEVER state a prerequisite, required course, test, score, GPA, age or grade limit, required document, fee, or eligibility condition that you did not read verbatim on a page you retrieved. Not from memory, not from what programs like this usually require, not from the program's name or subject. If you did not retrieve the page, you have no page-backed items — say so by emitting only generic ones. Inventing a prerequisite tells a student they are ineligible for something they can actually do, and they will not apply. An empty or dull list is a far better outcome than a confident wrong one.

If you are unsure which kind an item is, it is generic. If you cannot quote a page for a specific claim, drop the claim rather than the item: "Review the eligibility requirements" is a fine generic item; "Review prerequisite requirements (Algebra 2)" is not, unless the page says Algebra 2.

Keep every item tactical and administrative — the logistics of applying. Never give advice about the student's own project or how to approach its substance: you do not know what they are working on and must not assume or invent it.

For each action item also give your best-guess direct URL for where the student would go to do it — the specific application/submission portal, payment page, account sign-up, recommender form, or test registration. Use the most specific URL you actually saw during search; reuse the general apply/info URL if nothing more specific applies. Use null if you found no plausible page — never invent a URL path you did not see.`;

export type RawActionItem = NonNullable<TrackerInfo['action_items']>[number];

export interface NormalizedActionItem {
  id: string;
  text: string;
  url: string | null;
  state: string;
  basis: 'page' | 'generic';
  evidence: string | null;
}

function shapeActionItems(
  raw: RawActionItem[] | undefined,
  idPrefix: string,
  trustBasis: boolean,
): NormalizedActionItem[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((ai) => ai && typeof ai.text === 'string' && ai.text.trim())
    .slice(0, 5)
    .map((ai, i) => {
      const evidence = typeof ai.evidence === 'string' && ai.evidence.trim()
        ? ai.evidence.trim()
        : null;
      const pageBacked = trustBasis && ai.basis === 'page' && !!evidence;
      return {
        id: `${idPrefix}-t${i}`,
        text: ai.text.trim(),
        url: typeof ai.url === 'string' && ai.url.startsWith('http') ? ai.url : null,
        state: 'not_started',
        basis: (pageBacked ? 'page' : 'generic') as 'page' | 'generic',
        evidence: pageBacked ? evidence : null,
      };
    });
}

// Tasks from GET /api/opportunities/<id>/action-items. `basis` is honoured here and ONLY
// here, because that endpoint is the only place a task's quote has been checked against
// page text we fetched ourselves (page_text.quote_is_on_page / claim_is_supported).
export function normalizeVerifiedActionItems(
  raw: RawActionItem[] | undefined,
  idPrefix: string,
): NormalizedActionItem[] {
  return shapeActionItems(raw, idPrefix, true);
}

// Tasks a model produced in the browser — the fallback when an opportunity has no catalog
// row to have been verified against. EVERYTHING here is forced to 'generic', however
// confidently the model labelled it, and the deliberate consequence is that the Quest Log
// files all of it under "Typical steps — confirm on the site".
//
// This is not caution, it is accuracy. The client cannot fetch the program's page — the
// browser is blocked cross-origin, and /api/messages attaches only googleSearch, no
// web_fetch and no urlContext — so nothing on this path has ever seen the page a task
// claims to quote. Reading the model's own `basis:"page"` as proof would be taking its word
// for the one thing it has repeatedly got wrong; that is how "Review prerequisite
// requirements (Algebra 2)" reached a student's card in the first place. A claim is
// page-backed because a page backed it, never because a model said so.
export function normalizeUnverifiedActionItems(
  raw: RawActionItem[] | undefined,
  idPrefix: string,
): NormalizedActionItem[] {
  return shapeActionItems(raw, idPrefix, false);
}

// Extract structured tracking data (dates, action items, apply URL) for one opportunity.
// Web search is ON; the prompt is ported verbatim from script.js.
export async function extractTrackerInfo(
  callGemini: GeminiCall,
  opp: Opportunity,
): Promise<TrackerInfo> {
  const today = todayLabel();
  const thisYear = new Date().getFullYear();
  const nextYear = thisYear + 1;
  const root = baseDomain(opp.url ?? '');
  const system = `You extract structured tracking data for an extracurricular opportunity (program, internship, competition, or research position), for a high-school student's tracker. Today's date is ${today}.

YOU MUST use web_search to gather current information before answering — do not rely on training data alone.

GOAL: capture as many pertinent dates as you can find or reasonably estimate. Estimating from a prior cycle is expected and encouraged, not a fallback of last resort — a well-justified estimate is always better than an empty field.

SEARCH STEPS (do all of these, in order):
1. Start with the given URL.
2. Search "site:${root} ${nextYear}" and "site:${root} ${thisYear}" for a current/upcoming-cycle page — orgs often publish a separate year-specific page distinct from the evergreen landing page, which frequently omits the specific dates you actually need.
3. ALWAYS ALSO search for the most recent PAST cycle (e.g. "site:${root} ${thisYear} deadline" and, using the year before ${thisYear} — compute it yourself from ${today} — "site:${root} <that year>"), even if step 2 succeeded. This is your estimation basis and is mandatory, not optional: you need it either to confirm the pattern behind a found date or to construct an estimate when nothing current is posted.
4. Search "site:${root} FAQ", "how to apply", "key dates", "deadlines", "timeline" and check the best hits — specific program URLs sometimes point to outdated or archived pages while the org's current site has the live one.
5. Look explicitly for closure language: "cycle closed," "not running this year," "applications no longer accepted," etc. DISTINGUISH between: (a) current cycle is closed but program recurs (e.g., "2026 closed, 2027 opening Fall") → status="running" (the program itself is ongoing), still extract dates for the next cycle; (b) program is permanently discontinued (e.g., "no longer offered," "program ended") → status="not_running", do not estimate future dates. Evidence of recurrence ("Next cycle in Fall", "2027 details TBA", "Check back for 2027") → treat as "running" with forward-dated important_dates.

ESTIMATION LOGIC (single source of truth — apply in this order):
a. Found explicit current/upcoming-cycle dates → use them, was_estimated:false for those entries.
b. No current-cycle dates found, but you found last cycle's real dates AND the program looks recurring (no evidence it's discontinued) → roll each date forward by ~1 year (or to the next plausible occurrence), was_estimated:true, status:"running". This is the expected path when a new cycle's page isn't live yet — use it; don't default to "unknown."
b2. The current cycle publishes SOME real dates (very often just the deadline) but NOT the opening, and a prior cycle published both → do NOT simply roll last cycle's opening forward a year. Take the opens-to-deadline INTERVAL from the prior cycle and apply it to this cycle's real deadline — if applications opened 10 weeks before last cycle's deadline, estimate this cycle's opening 10 weeks before the posted one, was_estimated:true. When a cycle shifts, the interval survives and the calendar date does not, so this is the more accurate estimate, and it is the most common way an opening date can be recovered at all.
c. Found only a vague pattern (e.g. "opens in fall," "rolling through spring") → construct a concrete estimated date from it (pick a reasonable specific day within the stated window), was_estimated:true, explain the basis briefly in note.
d. Current cycle is explicitly closed (e.g., "2026 applications closed") BUT organization states or implies the program will recur (e.g., "2027 opens Fall 2026") → status:"running", extract/estimate dates for the future cycle from explicit month/season language, was_estimated:true. This is the expected path when a new cycle isn't yet open — capture the forward-looking dates.
e. Found genuinely nothing current AND nothing from any prior cycle after completing all search steps above → status:"unknown". This should be rare — only after step 3 has actually been tried and failed.

Important dates — this matters a lot; capture EVERY pertinent date, not just a single "deadline":
- This includes (when they exist or can be estimated): registration/application opens, early-bird deadline, regular/final deadline, notification/decision date, and event dates (e.g. a conference or symposium's actual start and end dates) — anything relevant in between. Many programs have MORE THAN ONE deadline — e.g. an early-bird/early registration deadline well before a later regular or final deadline (AMC 12's early-bird registration deadline is a good example: it lands weeks before the exam itself). Find and list EVERY distinct date you can, each with a short specific label (e.g. "Early Bird Registration", "Regular Registration", "Final Deadline", "Notification Date", "Conference Begins", "Conference Ends") and a "type" of "opens", "deadline", "event_start", "event_end", or "other", in chronological order. Do not collapse them into just one "final" date — the earliest one is often the one a student needs to act on first.
- Actively search for the OPENS date specifically, not just the close — this is the field most often missed, and it's often the single most useful date for a student trying to plan ahead. Estimate it from the prior cycle if not explicitly posted (was_estimated:true).
- An "opens" entry is REQUIRED whenever the program has any application or registration step. The tracker marks a program HAPPENING NOW the moment its first date has passed, so a program carrying only a deadline reads as "not started yet" right up until the day it closes — which is exactly backwards for a student who could be applying today. If the current cycle's opening isn't posted, project the prior cycle's (was_estimated:true). Omit it only if no cycle published one at all, and then say why in "note" (e.g. "rolling admissions, no published open date").
- Every date you reason about belongs in "important_dates", not just in "note". If you have enough basis to write a date into "note" (e.g. "registration typically opens Sept and closes Nov"), you have enough basis to ALSO add the matching structured entry to "important_dates" (was_estimated:true) — never describe a date in "note" prose without adding it. "note" is for a short caveat/basis explanation, not a place to put date information that should have been structured.
- This app exists so a student never misses a deadline, so an empty date field is the worst outcome and a well-founded estimate always beats it. A date WINDOW, month, season or range ("typically October-December", "opens in fall") MUST be materialised into a concrete important_dates entry (estimated:true), never left as prose: pick one representative day inside the window — the EARLIEST day for a "deadline" (bias early, because a date shown before the true one is fail-safe, while a date shown after it is the miss we exist to prevent), the FIRST day for "opens"/"event_start"/a bare season — keeping it on or after today and inside the window the note actually supports (the near edge, never a date conjured months before the range). Split a multi-part cycle (abstract deadline, regional event, national event) into one entry each, never one combined date. A merely typical/historical pattern ("usually in the fall") is NOT an excuse to leave it as prose — that is exactly what to materialise. If a window's early edge is already past but it still extends beyond today, use the earliest day still on or after today rather than rolling it a full year forward (which would hide a deadline still imminent this cycle); only roll a window forward a year when it lies entirely in the past.
- Only omit a date category if you found no information for it AND no prior-cycle basis to estimate it.
- If there's genuinely only one date, list just that one entry.

SELF-CHECK before responding:
- Every date in "important_dates" must be on or after today (${today}). If any is in the past, roll it forward to its next real occurrence (was_estimated:true) or drop it — never submit a past date.
- Never report an ESTIMATED date as today's date. An estimate must be what its own stated basis computes to — "~10 weeks before the January 10 deadline" is late October, not today. Anchoring an estimate to the current date makes a program read as open right now when it is not; omit the date instead if you cannot work out the real one.
- Set "estimated" PER DATE: true if that specific date came from a prior cycle, an interval, or a vague pattern; false only if it is explicitly posted for the current cycle. A row routinely mixes the two (a confirmed deadline beside a projected opening), the tracker shows this marker next to each date, and the row-level "was_estimated" cannot express the difference. Do NOT also write "(estimated)" into the label — the field is what gets rendered.
- Every date OR date-window mentioned in "note" — a specific day, a month, a season or a range — must have a matching structured entry in "important_dates", and vice versa — the two must agree.
- Prefer including a reasonably-estimated date over omitting it. Only leave a category out if step (e) above genuinely applies.

${ACTION_ITEM_RULES}

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON, matching exactly this schema: {"status":"running, not_running, or unknown","meta":"one short line: dates/location/fee/format, separated by ' · '","fit":"one sentence, under 25 words, on what this actually involves","note":"one sentence, under 25 words: status/estimate basis/caveat","noteType":"good, plain, or flag — use flag if not_running or a major caveat","important_dates":[{"label":"short specific label, e.g. 'Early Bird Registration'","date_iso":"YYYY-MM-DD","type":"opens, deadline, event_start, event_end, or other","estimated":true or false}],"deadline_label":"short text like ROLLING or TBA — only used when the important_dates array is empty","was_estimated":true or false,"requirements":[{"date":"short date text","text":"under 12 words — what's needed, not a repeat of an important_dates entry"}],"apply_url":"the best URL for actually applying","apply_label":"short button label like 'Apply now'","calendar_events":[{"date":"YYYY-MM-DD","text":"under 8 words","type":"deadline, opens, notify, or conference"}],"action_items":[{"text":"short concrete task, under 10 words","url":"best-guess direct URL for this specific action (submission portal, payment page, sign-up page, etc.), or null","basis":"page or generic","evidence":"verbatim quote from the retrieved page when basis is page, else null"}]}. Stay well within a 1000-token response: at most 4 important_dates entries, 3 requirements items, 3 calendar_events, and 5 action_items. Never truncate mid-value or leave the JSON unclosed — shorten or drop optional arrays first, but keep at least the earliest date if one exists.`;
  // Eligibility and the grade range are CURATED catalog columns, maintained by
  // refresh_opportunities.py. Until 2026-08-24 they were not in OPPORTUNITIES_FIELDS, so the
  // app never received them and this prompt never saw them — the one place in the system
  // that knows a program's real entry requirements was invisible to the prompt that was
  // inventing entry requirements. They are context, not proof: an action item still needs a
  // verbatim page quote to count as page-backed, and the prompt says so below.
  const grades = [opp.grade_min, opp.grade_max].some((g) => g !== null && g !== undefined)
    ? `Grades (from catalog): ${opp.grade_min ?? '?'}-${opp.grade_max ?? '?'}\n`
    : '';
  const eligibility = opp.eligibility ? `Eligibility (from catalog): ${opp.eligibility}\n` : '';
  const userContent = `Opportunity: ${opp.name} (${opp.org ?? ''})
URL: ${opp.url ?? ''}
Known info: ${opp.summary ?? ''}
${eligibility}${grades}
Fetch this URL (and the base site if needed), and extract current tracking details per the schema. Look carefully for every relevant date — registration open/close, event dates, notifications — not just the final deadline.

The catalog lines above are our own stored notes, not the program's page. Use them to know what to look for and to sanity-check what you find — never quote them as "evidence" for a page-backed action item, and never treat their absence as proof that a requirement does not exist.`;
  return callGeminiJSON<TrackerInfo>(callGemini, system, userContent, true);
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

${ACTION_ITEM_RULES}

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON: {"name":"program/opportunity name from the page, or organization name if no program name found, under 50 chars","section":"conferences, journals, researchCompetitions, pureCompetitions, internships, or summerPrograms","status":"running, not_running, or unknown","meta":"one short line: dates/location/fee/format","fit":"one sentence, under 25 words","note":"one sentence, under 25 words","noteType":"good, plain, or flag","important_dates":[{"label":"short label","date_iso":"YYYY-MM-DD","type":"opens, deadline, event_start, event_end, or other","estimated":true or false}],"deadline_label":"short text like ROLLING, only if important_dates is empty","was_estimated":true or false,"requirements":[{"date":"...","text":"under 12 words"}],"apply_url":"...","apply_label":"short button label","category":"short type label like 'Science fair' or 'Rationality camp', or null","action_items":[{"text":"short concrete task, under 10 words","url":"best-guess direct URL for this specific action, or null","basis":"page or generic","evidence":"verbatim quote from the retrieved page when basis is page, else null"}]}. Stay well within 1000 tokens: at most 4 important_dates, 3 requirements, and 5 action_items.`;
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
