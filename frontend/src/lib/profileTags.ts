import { callFeatureJSON, type FeatureCall } from './aiJson';
import { normalizeProfileBasics } from './ranking';

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

// The two output budgets — the extraction ceiling and the per-tag enrichment sizing — moved
// to app/services/prompts.py with the prompts they belong to (S1-1). The enrichment one is a
// function of the tag count, and the server has the same tag count this file did.

// A non-termination guard, NOT a size limit — it bounds retries, not tags. Every round asks
// for every tag still missing, so the shortfall shrinks fast (a round that fits most of a
// long list leaves only a short one behind). A round that adds nothing stops the loop
// immediately, so this is only reached when rounds keep making partial progress.
export const ENRICH_MAX_ROUNDS = 4;

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
  callFeature: FeatureCall,
  wanted: string[],
): Promise<Record<string, EnrichedTag>> {
  let arr: unknown[] = [];
  try {
    // The output budget is sized from the tag count SERVER-side now (S1-1) — it is a
    // function of this input, and the server can compute it from the same input.
    const parsed = await callFeatureJSON<unknown>(callFeature, 'tag_intent', { tags: wanted });
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
  callFeature: FeatureCall,
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
    const got = await enrichRequest(callFeature, pending);
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

export async function extractTagsAndBasics(
  callFeature: FeatureCall,
  text: string,
): Promise<ProfileExtract> {
  if (!text || !text.trim()) return { basics: {}, tags: [] };
  // callFeatureJSON, not one call + extractJSON: it retries once on a parse failure. Gemini
  // intermittently emits a stray character into an otherwise fine response (an observed run
  // opened with `[=` instead of `["`), and without the retry that one glitch silently cost
  // the student their whole filter facet — which is exactly what this writer exists for.
  const parsed = await callFeatureJSON<unknown>(callFeature, 'profile_extract', { text });
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

  return { basics, tags: await enrichProfileTags(callFeature, names, seed) };
}
