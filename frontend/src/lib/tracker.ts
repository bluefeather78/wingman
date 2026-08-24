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
  action_items?: { text: string; url: string | null }[];
  important_date_note?: string;
}

// The shared/cached deadline check is authoritative for status/important_dates/was_estimated
// when present (it's verified server-side and shared across every user tracking the same
// opportunity, unlike extractTrackerInfo's own per-call guess). Overlays in place.
export function applyDeadlineCheckToInfo(
  info: TrackerInfo,
  deadlineInfo: Partial<TrackerInfo> | null | undefined,
): void {
  if (!deadlineInfo) return;
  if (
    deadlineInfo.status &&
    ['running', 'not_running', 'unknown'].includes(deadlineInfo.status)
  ) {
    info.status = deadlineInfo.status;
  }
  if (Array.isArray(deadlineInfo.important_dates) && deadlineInfo.important_dates.length) {
    info.important_dates = deadlineInfo.important_dates;
  }
  if (typeof deadlineInfo.was_estimated === 'boolean') info.was_estimated = deadlineInfo.was_estimated;
  if (deadlineInfo.important_date_note) info.note = deadlineInfo.important_date_note;
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
c. Found only a vague pattern (e.g. "opens in fall," "rolling through spring") → construct a concrete estimated date from it (pick a reasonable specific day within the stated window), was_estimated:true, explain the basis briefly in note.
d. Current cycle is explicitly closed (e.g., "2026 applications closed") BUT organization states or implies the program will recur (e.g., "2027 opens Fall 2026") → status:"running", extract/estimate dates for the future cycle from explicit month/season language, was_estimated:true. This is the expected path when a new cycle isn't yet open — capture the forward-looking dates.
e. Found genuinely nothing current AND nothing from any prior cycle after completing all search steps above → status:"unknown". This should be rare — only after step 3 has actually been tried and failed.

Important dates — this matters a lot; capture EVERY pertinent date, not just a single "deadline":
- This includes (when they exist or can be estimated): registration/application opens, early-bird deadline, regular/final deadline, notification/decision date, and event dates (e.g. a conference or symposium's actual start and end dates) — anything relevant in between. Many programs have MORE THAN ONE deadline — e.g. an early-bird/early registration deadline well before a later regular or final deadline (AMC 12's early-bird registration deadline is a good example: it lands weeks before the exam itself). Find and list EVERY distinct date you can, each with a short specific label (e.g. "Early Bird Registration", "Regular Registration", "Final Deadline", "Notification Date", "Conference Begins", "Conference Ends") and a "type" of "opens", "deadline", "event_start", "event_end", or "other", in chronological order. Do not collapse them into just one "final" date — the earliest one is often the one a student needs to act on first.
- Actively search for the OPENS date specifically, not just the close — this is the field most often missed, and it's often the single most useful date for a student trying to plan ahead. Estimate it from the prior cycle if not explicitly posted (was_estimated:true).
- Every date you reason about belongs in "important_dates", not just in "note". If you have enough basis to write a date into "note" (e.g. "registration typically opens Sept and closes Nov"), you have enough basis to ALSO add the matching structured entry to "important_dates" (was_estimated:true) — never describe a date in "note" prose without adding it. "note" is for a short caveat/basis explanation, not a place to put date information that should have been structured.
- Only omit a date category if you found no information for it AND no prior-cycle basis to estimate it.
- If there's genuinely only one date, list just that one entry.

SELF-CHECK before responding:
- Every date in "important_dates" must be on or after today (${today}). If any is in the past, roll it forward to its next real occurrence (was_estimated:true) or drop it — never submit a past date.
- Every specific date/estimate mentioned in "note" must have a matching structured entry in "important_dates", and vice versa — the two must agree.
- Prefer including a reasonably-estimated date over omitting it. Only leave a category out if step (d) above genuinely applies.

Action items — think through what a student would actually need to DO to meet the nearest deadline, not just the deadline itself: e.g. requesting a recommendation letter, drafting an essay, gathering transcripts, preparing a portfolio or writing sample, getting parent/guardian sign-off, registering for a required test. Infer these from the requirements you find and from what's typical for this type of opportunity. Keep every item tactical and administrative — the logistics of applying, never advice about the student's own project or how to approach its substance, since you have no way of knowing the specifics of their work and must not assume or invent any. List 3-5 short, concrete action items (skip this if status is not_running).
For each action item, also give your best-guess direct URL for where the student would actually go to do it — the specific application/submission portal, payment or fee page, account sign-up/registration page, common-app or portal login, recommender/counselor form, or test-registration page, as applicable. Use the most specific URL you found during search (not just the homepage) whenever one exists. If nothing more specific than the general apply/info URL applies, reuse that URL. Only use null if you genuinely found no plausible page for that action — never invent or guess at a URL path that wasn't actually seen.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON, matching exactly this schema: {"status":"running, not_running, or unknown","meta":"one short line: dates/location/fee/format, separated by ' · '","fit":"one sentence, under 25 words, on what this actually involves","note":"one sentence, under 25 words: status/estimate basis/caveat","noteType":"good, plain, or flag — use flag if not_running or a major caveat","important_dates":[{"label":"short specific label, e.g. 'Early Bird Registration'","date_iso":"YYYY-MM-DD","type":"opens, deadline, event_start, event_end, or other"}],"deadline_label":"short text like ROLLING or TBA — only used when the important_dates array is empty","was_estimated":true or false,"requirements":[{"date":"short date text","text":"under 12 words — what's needed, not a repeat of an important_dates entry"}],"apply_url":"the best URL for actually applying","apply_label":"short button label like 'Apply now'","calendar_events":[{"date":"YYYY-MM-DD","text":"under 8 words","type":"deadline, opens, notify, or conference"}],"action_items":[{"text":"short concrete task, under 10 words","url":"best-guess direct URL for this specific action (submission portal, payment page, sign-up page, etc.), or null"}]}. Stay well within a 1000-token response: at most 4 important_dates entries, 3 requirements items, 3 calendar_events, and 5 action_items. Never truncate mid-value or leave the JSON unclosed — shorten or drop optional arrays first, but keep at least the earliest date if one exists.`;
  const userContent = `Opportunity: ${opp.name} (${opp.org ?? ''})\nURL: ${opp.url ?? ''}\nKnown info: ${opp.summary ?? ''}\n\nFetch this URL (and the base site if needed), and extract current tracking details per the schema. Look carefully for every relevant date — registration open/close, event dates, notifications — not just the final deadline.`;
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

Estimation is expected and encouraged, not a last resort — apply in order: (a) explicit current/upcoming-cycle dates found → use them, was_estimated:false; (b) no current-cycle dates but real prior-cycle dates found and the program looks recurring → roll each forward ~1 year, was_estimated:true, status:"running" (the expected path when a new cycle's page isn't live yet — don't default to "unknown"); (c) only a vague pattern found (e.g. "opens in fall") → construct a concrete estimated date from it, was_estimated:true, explain briefly in note; (d) genuinely nothing current or prior-cycle found after trying step 3 → status:"unknown" (should be rare).

Find EVERY pertinent date — registration opens, early-bird vs. regular deadline, notification date, and event/conference start-end dates — each with a short label and a "type" of "opens", "deadline", "event_start", "event_end", or "other", in chronological order. Pay particular, deliberate attention to the registration/application OPENS date, not just the deadline — this is the field most often missed. Only omit a date category if there's genuinely no basis to find or estimate one (per step d above). Every date you have enough basis to mention in "note" (e.g. "registration typically opens Sept") must ALSO appear as a matching "important_dates" entry (was_estimated:true) — never describe date info in "note" without a corresponding structured entry, and vice versa. Prefer including a reasonably-estimated date over omitting it.

Also think through 3-5 short, concrete action items a student would need to do to meet the nearest deadline (e.g. request a recommendation letter, draft an essay, gather transcripts) — infer these from requirements and what's typical for this type of opportunity. Keep every item tactical and administrative — the logistics of applying, never advice about the student's own project or its substance, since you don't know the specifics of their work and must not assume or invent any. Skip if status is not_running.
For each action item, also give your best-guess direct URL for where the student would actually go to do it — the specific application/submission portal, payment or fee page, account sign-up/registration page, or test-registration page, as applicable. Use the most specific URL you found during search (not just the homepage) whenever one exists; reuse the general apply/info URL if nothing more specific applies; use null only if you genuinely found no plausible page — never invent a URL path that wasn't actually seen.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON: {"name":"program/opportunity name from the page, or organization name if no program name found, under 50 chars","section":"conferences, journals, researchCompetitions, pureCompetitions, internships, or summerPrograms","status":"running, not_running, or unknown","meta":"one short line: dates/location/fee/format","fit":"one sentence, under 25 words","note":"one sentence, under 25 words","noteType":"good, plain, or flag","important_dates":[{"label":"short label","date_iso":"YYYY-MM-DD","type":"opens, deadline, event_start, event_end, or other"}],"deadline_label":"short text like ROLLING, only if important_dates is empty","was_estimated":true or false,"requirements":[{"date":"...","text":"under 12 words"}],"apply_url":"...","apply_label":"short button label","category":"short type label like 'Science fair' or 'Rationality camp', or null","action_items":[{"text":"short concrete task, under 10 words","url":"best-guess direct URL for this specific action, or null"}]}. Stay well within 1000 tokens: at most 4 important_dates, 3 requirements, and 5 action_items.`;
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
