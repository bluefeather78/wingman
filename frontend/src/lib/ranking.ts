import type { Opportunity } from '@/api/types';
import { callFeatureJSON, type FeatureCall } from './aiJson';
import { VALID_SUBJECTS } from './constants';
import { isGradeEligible } from './grade';

// The candidate-ranking chain, ported from script.js. Model access is injected as a
// `callFeature(feature, inputs) => Promise<FeatureResult>` so these modules stay pure,
// testable and independent of the auth wiring — pass `httpClient.callFeature` at the call
// site. The prompts themselves live server-side as of S1-1; what is left here is the
// pre-filter, the compaction, and the validation of what comes back.

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
//
// The prompts these used to carry moved to app/services/prompts.py in S1-1. What stays here
// is what is genuinely client-side: which subjects are valid (the answer is filtered against
// the same list the prompt names — a test asserts the two agree), and the compaction of the
// candidate rows, which decides how much of the catalog leaves this device.
export async function inferSubjects(
  callFeature: FeatureCall,
  description: string,
): Promise<string[]> {
  const arr = await callFeatureJSON<unknown>(callFeature, 'infer_subjects', { description });
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
  callFeature: FeatureCall,
  description: string,
  candidates: Opportunity[],
  prefs: string | null,
  requireAll: boolean,
): Promise<RankedPick[]> {
  // Still compacted here, and deliberately: this is the payload leaving the device, and the
  // nine fields the ranker reads are a fraction of a catalog row.
  const compact = candidates.map((c) => ({
    id: c.id, name: c.name, org: c.org, summary: c.summary, subject_tags: c.subject_tags,
    type: c.type, price: c.price, location: c.location, season: c.season,
  }));
  const arr = await callFeatureJSON<unknown>(callFeature, 'ranking', {
    description, candidates: compact, prefs, requireAll,
  });
  return Array.isArray(arr) ? (arr as RankedPick[]) : [];
}

// The five "basics" tiles are read out of the profile prose rather than collected as a form.
export const PROFILE_BASICS_FIELDS = [
  { key: 'grade', label: 'Grade level' },
  { key: 'state', label: 'Home state' },
  { key: 'gender', label: 'Gender' },
] as const;

// PROFILE_BASICS_RULE moved to app/services/prompts.py with the prompts that embed it
// (S1-1). What stays here is normalizeProfileBasics below, which validates the ANSWER —
// that is client-side work and always was.

// extractProfileBasics was DELETED by S1-1, not ported: nothing called it (the basics tiles
// read the merged profile_extract slot instead), and porting a dead prompt server-side would
// have kept a model call reachable that nothing needed.

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
