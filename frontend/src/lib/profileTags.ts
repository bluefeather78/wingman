import { callGeminiJSON, type GeminiCall } from './aiJson';
import { normalizeProfileBasics, PROFILE_BASICS_RULE } from './ranking';

// The "Your Profile" filter facet's tags — the retired SPA's `filterTags` slot
// (PROFILE_DERIVED_SLOTS), ported. The RN app READ this slot but never wrote it, so the
// facet only ever worked for accounts carrying tags from the old app — and the profile
// save destroyed even those. This is the missing writer.
//
// Tags are BROAD THEMES, not one tag per line of the profile. The prompt used to ask for
// "one entry for every distinct interest, goal, project or pursuit it mentions" and to forbid
// merging two different pursuits, so a 24-line profile produced 24 rows in the dropdown: two
// separate volunteering placements, three separate clubs, one row for a single trivia night.
// A facet at that altitude cannot filter anything — every row matched one program or none,
// and the student scrolled a copy of their own resume looking for a search term.
//
// It now asks for a mutually-exclusive, collectively-exhaustive set: every item in the profile
// lands in exactly one theme, and a theme is pitched at the level a program is described
// ("Volunteering with organizations that serve children", not "Volunteering with Kids Coming
// Together"). Ties are broken by WHERE THE OPPORTUNITIES LIVE — a chatbot built to learn AI
// groups with studying AI, a chatbot being sold groups with building products — because that
// is the question the facet exists to answer.
//
// COVERAGE STILL MEANS COVERAGE: nothing may be dropped for being small, old or unimpressive.
// Grouping is not shortening. An item that fits nowhere joins the nearest theme rather than
// disappearing, which is the failure mode a "keep the list short" instruction would produce.
//
// Two calls total, whatever the theme count: the merged pass returns tags already enriched, and
// the enrichment loop only tops up what did not come back. Enriching each tag in its own request
// made a results page cost 1 + N round trips, the slowest of which gated the whole filter bar.
//
// There is deliberately NO CAP anywhere here — the 6-12 in the prompt is guidance to the model,
// never a `.slice()`. A hard cap silently dropped whatever fell off the end, and it fell off by
// the model's own notion of "most important", so a student with a broad profile lost their
// less-central interests with nothing on screen to say so. Grouping reduces the count for a
// reason a reader can see; truncation does not.
//
// What a cap was implicitly standing in for was the OUTPUT BUDGET: both answers grow with the
// profile, and at the uniform MESSAGES_MAX_TOKENS a broad one truncated — invisibly, because
// extractJSON repairs a truncated array rather than failing, so a short answer and a complete
// one look identical. That is addressed at the source: both calls send their own `maxTokens`
// (clamped server-side, never below the default), and enrichment TOPS UP — it re-asks for
// exactly the tags that did not come back, so a shortfall is repaired rather than capped.
// The facet dropdown scrolls, so a long list stays reachable.

export interface EnrichedTag {
  tag: string;
  intent?: string;
  nextSteps?: string[];
}

// Output budget for the extraction call. The tag count is unknown by definition before it
// runs — that is what it is computing — so it simply asks for the ceiling. Unused budget is
// free (billing is on tokens produced), the same reason profile synthesis asks generously.
export const TAG_EXTRACT_MAX_TOKENS = 8000;

// Enrichment budget, sized from the tag count rather than fixed. An enrichment object is a
// tag (<= 60 chars) plus a one-sentence intent plus 2-3 short next steps; ~90 tokens covers
// one comfortably, and the overhead term leaves room for JSON scaffolding and for Gemini 3.x
// thinking tokens, which draw from this SAME budget.
export const ENRICH_TOKENS_PER_TAG = 90;
export const ENRICH_TOKEN_OVERHEAD = 600;

// A non-termination guard, NOT a size limit — it bounds retries, not tags. Every round asks
// for every tag still missing, so the shortfall shrinks fast (a round that fits most of a
// long list leaves only a short one behind). A round that adds nothing stops the loop
// immediately, so this is only reached when rounds keep making partial progress.
export const ENRICH_MAX_ROUNDS = 4;

export function enrichBudgetFor(tagCount: number): number {
  return ENRICH_TOKEN_OVERHEAD + tagCount * ENRICH_TOKENS_PER_TAG;
}

// Deduped case-insensitively, and never truncated. Duplicates matter here because
// `enrichProfileTags` keys its results by tag string and the facet renders one row per tag
// with the tag as its React key — so a repeat would collide in both places.
export function dedupeTagStrings(tags: unknown[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  tags.forEach((t) => {
    if (typeof t !== 'string' || !t.trim()) return;
    const tag = t.trim();
    const key = tag.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push(tag);
  });
  return out;
}

// One request for the tags it is given. Returns what it could parse, keyed by tag; never
// throws, so a failed request costs its tags' enrichment rather than every tag's.
async function enrichRequest(
  callGemini: GeminiCall,
  wanted: string[],
): Promise<Record<string, EnrichedTag>> {
  const system = `You are helping match a high school student's interests/goals to the best opportunities. You will be given a list of the student's profile themes — each one covers a whole area of what they do, not a single project. Analyze EACH theme for what it represents and what would best help them grow.

Return ONLY a JSON array with one object per tag, in the same order as given, no other text:
[{
  "tag": "the tag string exactly as given",
  "intent": "what they want out of this whole area (1 short sentence)",
  "nextSteps": ["2-3 short, logical milestones for the AREA, e.g. Master advanced techniques", "Enter competitions"]
}]

Return an object for EVERY tag listed, however many there are — do not stop early and do not summarise. Keep every field short so the whole array fits in one response.`;
  const userContent = `PROFILE THEMES:\n${wanted.map((t, i) => `${i + 1}. ${t}`).join('\n')}\n\nReturn the JSON array of ${wanted.length} enrichment objects.`;

  let arr: unknown[] = [];
  try {
    const parsed = await callGeminiJSON<unknown>(
      callGemini, system, userContent, false, enrichBudgetFor(wanted.length),
    );
    // Tolerate the model wrapping the array in an object ({"tags": [...]}) — the shape it
    // was asked for is an array, but a wrapper is a formatting slip, not a failed answer.
    arr = Array.isArray(parsed)
      ? parsed
      : parsed && typeof parsed === 'object'
        ? (Object.values(parsed as Record<string, unknown>).find(Array.isArray) as unknown[]) || []
        : [];
  } catch (e) {
    console.warn('Profile tag enrichment request failed:', (e as Error).message);
  }

  // Match on the echoed tag string where there is one, falling back to position — a
  // truncated or reordered response still yields usable enrichments for the tags it did
  // return, instead of throwing all of them away. Position is relative to THIS request's
  // list, which is why `wanted` is threaded through rather than the full tag set: on a
  // top-up round the indexes mean nothing against the original ordering.
  const byTag: Record<string, EnrichedTag> = {};
  arr.forEach((e, i) => {
    if (!e || typeof e !== 'object') return;
    const rec = e as { tag?: unknown };
    const tag = typeof rec.tag === 'string' && wanted.includes(rec.tag) ? rec.tag : wanted[i];
    if (tag && !byTag[tag]) byTag[tag] = { ...(e as EnrichedTag), tag };
  });
  return byTag;
}

// Enrich every tag: ALL of them in one request, then re-ask for whatever did not come back.
// The top-up is what lets the tag count be unbounded without a per-request size limit — a
// response that could not fit the whole list is a shortfall to repair, not a cap to accept.
async function enrichProfileTags(
  callGemini: GeminiCall,
  tags: string[],
  // Enrichments already in hand. The merged extraction pass returns tags WITH their intent
  // and next steps, so it seeds this and the loop below usually has nothing left to do —
  // it degrades to a no-op rather than a second call.
  seed: Record<string, EnrichedTag> = {},
): Promise<EnrichedTag[]> {
  if (!tags.length) return [];
  const byTag: Record<string, EnrichedTag> = { ...seed };
  let pending = tags.filter((t) => !byTag[t]);
  for (let round = 0; round < ENRICH_MAX_ROUNDS && pending.length; round++) {
    const got = await enrichRequest(callGemini, pending);
    const before = Object.keys(byTag).length;
    Object.assign(byTag, got);
    // No progress means re-asking is not going to start working — a failed call, or a model
    // that will not answer for these tags. Stop rather than spend the remaining rounds.
    if (Object.keys(byTag).length === before) break;
    pending = tags.filter((t) => !byTag[t]);
    if (pending.length) {
      console.warn(`Profile tag enrichment: ${pending.length} of ${tags.length} tags did not come back; re-asking.`);
    }
  }
  // Every extracted tag survives, enriched or not. Enrichment only sharpens the scoring
  // prompt (the scorer already substitutes for a missing intent or nextSteps), so dropping
  // un-enriched tags traded a slightly weaker facet for no facet at all — which is how one
  // malformed response emptied the whole dropdown.
  return tags.map((t) => byTag[t] || { tag: t });
}

// ---------------------------------------------------------------------------------------
// The MERGED pass: tags (already enriched) and the basics tiles, from ONE request.
//
// These used to be three separate calls — extract tag strings, enrich them, extract basics —
// which meant the same profile text was uploaded and re-read three times to answer three
// questions about it. They are all pure functions of that one text and they all run at the
// same moment (every synthesis invalidates all of them together), so the split bought
// nothing and cost three round trips plus three copies of the input.
//
// `inferSubjects` is deliberately NOT folded in, even though it would fit: it is the one
// derived value on the search critical path (preFilter needs it before it can narrow the
// catalog), and this answer is the slowest of the set because it carries every tag. Merging
// it would make a cold-cache search wait on tag enrichment it does not use. The chat opener
// pool is out for a different reason — it runs on Anthropic, and moving it here would
// silently change its provider and mis-attribute its cost.
export interface ProfileExtract {
  basics: Record<string, string | null>;
  tags: EnrichedTag[];
}

// Budget: the basics object and the tag array in one response, so the per-tag term plus the
// extraction headroom. Unused budget is free (billing is on tokens produced).
export function mergedExtractBudget(): number {
  return TAG_EXTRACT_MAX_TOKENS;
}

export async function extractTagsAndBasics(
  callGemini: GeminiCall,
  text: string,
): Promise<ProfileExtract> {
  if (!text || !text.trim()) return { basics: {}, tags: [] };
  const system = `You are reading a high school student's profile and pulling out everything an opportunity-matching app needs from it, in ONE pass. Return ONLY a raw JSON object, no markdown and no preamble, with exactly two keys: "basics" and "tags".

"basics" is an object with exactly these keys: ${PROFILE_BASICS_RULE}

"tags" is the student's whole profile reduced to a small set of BROAD THEMES. This is a filter facet, not a resume: each theme becomes one row in a dropdown, and picking it searches a catalog of programs, competitions and internships for things that fit it. A theme nobody could search for is a wasted row.

First sweep the profile for raw material - current projects and research; interests they want to go deeper in; interests mentioned but never started; academic goals such as competitions, scores and certifications; career or industry aspirations; leadership and organizing; service and volunteering; hobbies and crafts. Then GROUP that material into themes. The grouping is the job; the sweep only makes sure nothing is missed.

Two rules govern the grouping:

MUTUALLY EXCLUSIVE - every item belongs to exactly ONE theme. When an item could sit in two, put it where the opportunities that would help with it live: a chatbot built to learn AI belongs with studying AI, a chatbot being sold to users belongs with building products; a physics olympiad score belongs with competitions, not with physics as a subject. If two themes would surface the same programs, they are one theme - merge them.

COLLECTIVELY EXHAUSTIVE - everything in the profile lands somewhere. Nothing is dropped for being small, old or unimpressive. A single passing mention joins the nearest theme; it does not get a theme of its own and it is not deleted.

Get the altitude right. A theme names a DIRECTION the student is pursuing, pitched at the level a program is described:
- TOO SPECIFIC - one project, club, role, event, organization or achievement. Never emit these: "Founded Linguistics Club", "Organized school Trivia Night", "Volunteering with Kids Coming Together", "Improving USAPhO score", "Making fresh pasta from scratch".
- RIGHT - "Organizing student clubs and enrichment events", "Volunteering with organizations that serve children", "Competing in STEM olympiads and contests", "Cooking and baking from scratch".
- TOO BROAD - a whole field of human activity: "STEM", "Science", "The arts", "Community service", "Leadership". If a theme would match most of a catalog of extracurriculars, split it.
Test each theme: it should either cover TWO OR MORE things in the profile, or be a standing interest broad enough that several different programs could serve it. If it covers exactly one line and nothing else would fit it, you are too specific - widen it until a sibling fits.

Past and present merge. Something done last year and something happening now belong to the same theme when they point the same way. Write every theme in the present, as an ongoing direction, never as a past accomplishment.

A rich profile usually reduces to 6-12 themes and a thin one to 3-5. Never return one theme per profile line, and never invent a theme for something the profile does not say. Order them most important first: the themes carrying the most of the profile, and the ones the student says they want to go further in.

Each entry is an object:
{
  "tag": "the theme, 3-8 words, plain and searchable, max 60 characters",
  "intent": "what the student wants out of this whole area (1 short sentence)",
  "nextSteps": ["2-3 short milestones for the AREA, e.g. Enter a national competition"]
}

Worked example - a profile mentioning a Linguistics Club she founded, a Math Club she co-founded, a school Trivia Night she ran, tutoring friends in chemistry, and two years volunteering at a children's outdoor program yields TWO themes, not five: "Organizing student clubs and enrichment events" and "Mentoring and volunteering with young people".`;
  const userContent = `STUDENT PROFILE:\n\n${text}\n\nReturn the JSON object with "basics" and "tags" only.`;

  // callGeminiJSON, not callGemini + extractJSON: it retries once on a parse failure.
  // Gemini intermittently emits a stray character into an otherwise fine response (an observed
  // run opened with `[=` instead of `["`), and without the retry that one glitch silently cost
  // the student their whole filter facet — which is exactly what this writer exists for.
  const parsed = await callGeminiJSON<unknown>(
    callGemini, system, userContent, false, mergedExtractBudget(),
  );
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { basics: {}, tags: [] };
  }
  const rec = parsed as { basics?: unknown; tags?: unknown };

  // Each half is salvaged on its own. One malformed key must not cost the other: a garbled
  // "basics" should not empty the filter dropdown, and vice versa. This is the whole reason
  // merging three calls into one is acceptable at all.
  const basics = normalizeProfileBasics(rec.basics);

  const rawTags = Array.isArray(rec.tags) ? rec.tags : [];
  // Accept either shape: an object per tag (what is asked for) or a bare string (a common
  // slip). A bare string still yields a usable tag; it just arrives un-enriched and is
  // topped up below like any other gap.
  const seed: Record<string, EnrichedTag> = {};
  const names = dedupeTagStrings(
    rawTags.map((t) => (t && typeof t === 'object' ? (t as { tag?: unknown }).tag : t)),
  );
  rawTags.forEach((t) => {
    if (!t || typeof t !== 'object') return;
    const e = t as EnrichedTag;
    if (typeof e.tag !== 'string') return;
    const tag = e.tag.trim();
    if (!names.includes(tag) || seed[tag]) return;
    if (e.intent || (Array.isArray(e.nextSteps) && e.nextSteps.length)) seed[tag] = { ...e, tag };
  });

  return { basics, tags: await enrichProfileTags(callGemini, names, seed) };
}
