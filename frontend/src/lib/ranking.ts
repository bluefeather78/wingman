import type { Opportunity } from '@/api/types';
import { callGeminiJSON, type GeminiCall } from './aiJson';
import { VALID_SUBJECTS } from './constants';
import { isGradeEligible } from './grade';

// The candidate-ranking chain, ported from script.js. All model access is injected as
// a `callGemini(system, userContent, useWebSearch) => Promise<string>` so these modules
// stay pure, testable, and independent of the (Phase-2) auth wiring — pass
// `apiClient.callGemini` at the call site.
export type { GeminiCall };

// ---------- Keyword pre-filter ----------
const STOPWORDS = new Set([
  'the','a','an','and','or','but','of','to','in','on','for','with','is','are','was','were',
  'be','been','being','it','its','this','that','these','those','i','my','me','we','our','you',
  'your','as','at','by','from','into','about','also','can','will','would','could','should',
  'have','has','had','not','no','so','if','than','then','which','who','what','when','where',
  'how','more','most','some','such','just','like','using','use','used',
]);

export function tokenize(text: string | null | undefined): string[] {
  return (text || '').toLowerCase().match(/[a-z0-9']+/g) || [];
}

export function keywordScore(tokens: string[], opp: Opportunity): number {
  const haystack = (
    `${opp.name} ${opp.org ?? ''} ${opp.summary ?? ''} ${(opp.subject_tags || []).join(' ')}`
  ).toLowerCase();
  let score = 0;
  tokens.forEach((t) => {
    if (STOPWORDS.has(t) || t.length < 3) return;
    if (haystack.includes(t)) score += 1;
  });
  return score;
}

// What preFilter did to the type constraint, so the caller can TELL the student rather
// than silently showing them a different kind of opportunity than they asked for.
//  - `widened`: a type was requested but too few rows carried it, so the whole catalog was
//    searched instead. `typeMatches` is how many rows of that type actually exist.
//  - `strictEmpty`: a strict-type kind (Conference/Journal) with zero rows in the catalog.
//    The pool comes back EMPTY rather than silently widening — see below.
export interface PreFilterResult {
  pool: Opportunity[];
  typeMatches: number;
  widened: boolean;
  strictEmpty: boolean;
}

// Below this many rows of the requested type, the type filter is abandoned rather than
// handing the ranker a pool too thin to rank. Named because two places reason about it.
export const TYPE_FILTER_MIN_POOL = 15;

// Narrow the full catalog to a scored candidate pool. `opportunities` is passed in
// (it was the global OPPORTUNITIES in script.js).
export function preFilter(
  opportunities: Opportunity[],
  description: string,
  subjectHints: string[] | null,
  typeFilter: string[] | null,
  strict: boolean,
  studentGrade: number | null,
): PreFilterResult {
  const tokens = [...new Set(tokenize(description).filter((t) => !STOPWORDS.has(t) && t.length >= 3))];
  const subjSet = new Set((subjectHints || []).map((s) => s.toLowerCase()));
  const typeSet = typeFilter && typeFilter.length ? new Set(typeFilter) : null;

  let base = opportunities;
  let typeMatches = 0;
  let widened = false;
  if (typeSet) {
    const byType = opportunities.filter((o) => o.type != null && typeSet.has(o.type));
    typeMatches = byType.length;
    // Only hard-filter by type if it leaves a reasonable pool; `strict` skips the size
    // gate for kinds whose Type is rare but exact (Conference/Journal Venue).
    if (byType.length >= TYPE_FILTER_MIN_POOL || (strict && byType.length > 0)) {
      base = byType;
    } else if (strict) {
      // A strict kind with NOTHING of its type must not fall through to the whole catalog.
      // rankCandidates sends `requireAll` for these ("return EVERY candidate, do not omit
      // any"), so widening here would order the model to return 100 wrong-type rows as if
      // they were an exhaustive list of the real ones. Empty pool, and the caller says so.
      return { pool: [], typeMatches: 0, widened: false, strictEmpty: true };
    } else {
      widened = true;
    }
  }
  if (studentGrade != null) {
    // Hard filter, no size-gate: nearly all rows have null grade bounds, so this only
    // excludes rows with a real range that doesn't cover the student.
    base = base.filter((o) => isGradeEligible(o, studentGrade));
  }

  const scored = base.map((opp) => {
    let score = keywordScore(tokens, opp);
    if ((opp.subject_tags || []).some((t) => subjSet.has((t || '').toLowerCase()))) score += 3;
    return { opp, score };
  });
  scored.sort((a, b) => b.score - a.score);
  const withScore = scored.filter((s) => s.score > 0);
  // Capped at 100: rankCandidates keeps only the best 10-12, and a smaller payload
  // means the model reads less before responding.
  const pool = (withScore.length >= 60 ? withScore : scored).slice(0, 100).map((s) => s.opp);
  return { pool, typeMatches, widened, strictEmpty: false };
}

// ---------- Model-backed steps ----------
export async function inferSubjects(
  callGemini: GeminiCall,
  description: string,
): Promise<string[]> {
  const system = `You infer which subject categories from a fixed list best match a student's passion-project description. Valid categories (use these exact strings): ${VALID_SUBJECTS.join(', ')}. Respond with ONLY a raw JSON array of 2-5 of the most relevant category strings, no markdown, no preamble. Example: ["Computer Science","STEM","Mathematics"]`;
  const arr = await callGeminiJSON<unknown>(callGemini, system, description, false);
  return Array.isArray(arr)
    ? (arr.filter((s): s is string => typeof s === 'string' && (VALID_SUBJECTS as readonly string[]).includes(s)))
    : [];
}

export interface RankedPick {
  id: string;
  reason: string;
  tier: 'strong' | 'look';
}

export async function rankCandidates(
  callGemini: GeminiCall,
  description: string,
  candidates: Opportunity[],
  prefs: string | null,
  requireAll: boolean,
): Promise<RankedPick[]> {
  const compact = candidates.map((c) => ({
    id: c.id, name: c.name, org: c.org, summary: c.summary, subject_tags: c.subject_tags,
    type: c.type, price: c.price, location: c.location, season: c.season,
  }));
  // requireAll (strict-type kinds like Conference/Journal Venue): rank every candidate
  // rather than omitting weak fits, or a tiny real pool zeroes out into a false "no matches".
  const selectionRule = requireAll
    ? 'Rank and return EVERY candidate given — this is an exhaustive list of the only known real options of this type, so do not omit any even if the fit is loose.'
    : 'Select ONLY the opportunities that would genuinely help them grow this specific project, build relevant skills, get recognition for it, or connect with the right community — not just anything thematically adjacent. Leave out weak or generic fits entirely; every opportunity you return must be a genuinely good match. Rank the best 10-12 matches only.';
  const system = `You are Wingman, helping a student find the best-fit extracurricular opportunities (programs, internships, competitions, research positions) for their specific passion project, from a candidate list. Read their project description and preferences carefully. ${selectionRule} For each, write the reason as the WOW moment — it should make the student feel genuinely SEEN, like a mentor who knows both them and this opportunity picked it for them. Draw a concrete line connecting TWO specific halves: (1) a SPECIFIC thing about THEM from their description/preferences below — a project, skill, goal, or next step they stated — and (2) a SPECIFIC thing THIS opportunity actually offers, from its own name/summary — what they would build, compete in, publish, research, or walk away with. The more precisely the two halves lock together, the better. Write 1-2 sentences, roughly 20-40 words; every clause must carry real information, never filler. GOAL-FORMAT: when their stated goal or next step names an outcome, prefer AND frame opportunities whose FORMAT delivers it — "publish" → a journal or conference; "compete/win" → a competition; "get mentorship / go deeper" → a research program or lab; "launch/build a product" → a build program, accelerator, or hackathon. GOOD: "You're taking Adio from concept to market — this accelerator gets you to your first real users and a pitch in front of investors." GOOD: "Your grapheme-to-phoneme research is exactly what this olympiad rewards — you'd crack original computational-linguistics problems against the strongest students." BAD (thin, could be anyone): "Great fit for your software interest." BAD (vague filler): "A wonderful chance to learn and grow." BAD (invented — never do this): naming a mentor, prize, cohort size, or feature the opportunity's own text never states. Use ONLY real details from their description and the opportunity's own text, and write it in second person ("you"/"your"), never third person ("the student"/"their"). Assign a tier: 'strong' (excellent, highly specific fit) or 'look' (solid, worth a look). Respond with ONLY a raw JSON array, no markdown, no preamble, no text after the array, matching: [{"id":"...","reason":"...","tier":"strong|look"}]. Stay within a 1500-token response; 10-12 items is a hard cap.`;
  const prefsText = prefs ? `\n\nStudent preferences: ${prefs}` : '';
  const userContent = `Student's passion project:\n${description}${prefsText}\n\nCandidate opportunities (JSON):\n${JSON.stringify(compact)}\n\nSelect and rank the best matches per the schema.`;
  const arr = await callGeminiJSON<unknown>(callGemini, system, userContent, false);
  return Array.isArray(arr) ? (arr as RankedPick[]) : [];
}

// The five "basics" tiles are read out of the profile prose rather than collected as a form.
export const PROFILE_BASICS_FIELDS = [
  { key: 'grade', label: 'Grade level' },
  { key: 'state', label: 'Home state' },
  { key: 'gender', label: 'Gender' },
] as const;

// The field list and the never-guess rule, shared with the merged extraction pass so both
// prompts describe the same three fields the same way.
export const PROFILE_BASICS_RULE =
  '"grade" (their school year, e.g. "11th grade"), "state" (US state or region they live in, spelled out), "gender". Set a key to null if the student did not say it — never guess, never infer from stereotypes, and never fill a value in just to avoid a null.';

export async function extractProfileBasics(
  callGemini: GeminiCall,
  text: string,
): Promise<Record<string, string | null>> {
  if (!text || !text.trim()) return {};
  const system = `You read a high school student's self-description and pull out a small set of specific profile facts, ONLY if the student actually stated or clearly implied them. Respond with ONLY a raw JSON object (no markdown, no preamble) with exactly these keys: ${PROFILE_BASICS_RULE}`;
  const obj = await callGeminiJSON<Record<string, unknown>>(callGemini, system, text, false);
  return normalizeProfileBasics(obj);
}

// Split out of the call above so the merged profile-extraction pass (profileTags.ts) applies
// exactly the same rules to the same fields. A second copy of "what counts as a stated fact"
// would let the two paths disagree about the same student.
export function normalizeProfileBasics(obj: unknown): Record<string, string | null> {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return {};
  const rec = obj as Record<string, unknown>;
  const out: Record<string, string | null> = {};
  PROFILE_BASICS_FIELDS.forEach(({ key }) => {
    const v = rec[key];
    out[key] =
      typeof v === 'string' && v.trim() && !/^(null|n\/?a|unknown|unspecified)$/i.test(v.trim())
        ? v.trim()
        : null;
  });
  return out;
}
