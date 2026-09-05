import type { FeatureCall } from './aiJson';
import { countProfileWords } from './profile';
import { parseGradeFromText } from './grade';
import { inferSubjects } from './ranking';
import { extractTagsAndBasics, type EnrichedTag, type ProfileExtract } from './profileTags';
import { starterQuestionPoolFromAI } from './profileChat';

// ONE call, not one per provider. This used to be `{ gemini, claude }`, because picking the
// provider meant picking an endpoint and the chat openers are the profile chat's deliberate
// Anthropic holdout — so a slot that called the wrong one would silently move a feature
// across providers and mis-attribute its cost. As of S1-1 the provider is a property of the
// FEATURE and lives server-side, so there is nothing left here to get wrong.
export type ModelCalls = FeatureCall;

// PROFILE_DERIVED_SLOTS, restored from the retired SPA. These are the values derived from
// the profile text by a model call: they depend on NOTHING but that text, which is exactly
// what makes them cacheable. The RN port dropped this entirely and re-derived subjects on
// every single search, so an unchanged profile paid for a fresh inferSubjects call per
// search AND could return different subjects each time — which decides what reaches the
// ranker, so the same profile could produce a full page of matches or an empty one.
//
// They are stored as INDEPENDENT slots rather than one record, because their consumers
// can't wait on each other: a search needs subjects before it can pre-filter, while tag
// building is two calls that would otherwise sit in front of it for nothing (the tags only
// feed a filter dropdown further down the page). Each slot carries its own copy of the text
// it was computed from, so one can be stale, missing, or mid-refresh without saying anything
// about the others.

export interface SlotRecord {
  // The exact profile text these values were computed from — the whole freshness test.
  profile?: string;
  // Informational only (it was the freshness test before that became exact-text identity);
  // kept because it is already on every stored record and reads well in a debug dump.
  wordCount?: number;
  computedAt?: string;
  [key: string]: unknown;
}

export interface FilterValuesSlot extends SlotRecord {
  subjects: string[];
  grade: number | null;
}
export interface FilterTagsSlot extends SlotRecord {
  enrichedTags: EnrichedTag[];
}
export interface BasicsSlot extends SlotRecord {
  fields: Record<string, string | null>;
}
export interface StarterPoolSlot extends SlotRecord {
  questions: string[];
}

export type SlotName = 'filterValues' | 'filterTags' | 'basics' | 'starterPool';

// The stored `student-profile` record. Slots live alongside the profile itself; anything
// writing this record must carry the other keys through untouched.
export interface ProfileRecord {
  synthesized?: string;
  updatedAt?: string | null;
  chatRounds?: number;
  filterValues?: FilterValuesSlot;
  filterTags?: FilterTagsSlot;
  basics?: BasicsSlot;
  starterPool?: StarterPoolSlot;
  [key: string]: unknown;
}

// Injected persistence, so this module stays free of the API client (and testable).
export interface ProfileStore {
  load: () => Promise<ProfileRecord | null>;
  save: (record: ProfileRecord) => Promise<void>;
}

interface SlotConfig {
  // Is the stored record actually filled in? Note the freshness check below keys on
  // `computedAt`, NOT on array length: an empty result is a legitimate answer (inferSubjects
  // drops anything outside VALID_SUBJECTS; a thin profile may yield no tags), and treating
  // it as "not computed yet" would re-pay for that same empty answer on every load.
  isFilled: (rec: SlotRecord) => boolean;
  compute: (calls: ModelCalls, text: string) => Promise<SlotRecord>;
}

// `filterTags` and `basics` are two slots served by ONE model call. They were two separate
// calls (three, with tag enrichment) that each uploaded and re-read the same profile text to
// answer a different question about it — and they always run together, because every
// synthesis invalidates every slot at once. Merging them removes two round trips and two
// copies of the input per profile update.
//
// They stay SEPARATE SLOTS despite sharing a call: each keeps its own stored copy of the
// text it was computed from, so one can be missing or mid-refresh without saying anything
// about the other, and a partial write leaves the other slot valid.
//
// Memoized by exact text and held (not cleared on settle) so a later lone reader — one slot
// stale, the other fresh — reuses the answer instead of paying for the pair again. A
// rejection is dropped immediately, or one failure would be cached as the permanent answer.
let sharedExtract: { text: string; promise: Promise<ProfileExtract> } | null = null;

function tagsAndBasics(calls: ModelCalls, text: string): Promise<ProfileExtract> {
  if (sharedExtract && sharedExtract.text === text) return sharedExtract.promise;
  const promise = extractTagsAndBasics(calls, text);
  promise.catch(() => {
    if (sharedExtract && sharedExtract.promise === promise) sharedExtract = null;
  });
  sharedExtract = { text, promise };
  return promise;
}

const SLOTS: Record<SlotName, SlotConfig> = {
  // Deliberately its own call, and the only Gemini slot that is. This is the one derived
  // value on the search critical path — preFilter cannot narrow the catalog without it —
  // whereas the merged pass above is the slowest answer of the set because it carries every
  // tag. Folding this in would make a cold-cache search block on tag enrichment it never
  // reads, to save one call on a path the student is waiting on.
  filterValues: {
    isFilled: (r) => Array.isArray((r as FilterValuesSlot).subjects),
    async compute(calls, text) {
      return { subjects: await inferSubjects(calls, text), grade: parseGradeFromText(text) };
    },
  },
  filterTags: {
    isFilled: (r) => Array.isArray((r as FilterTagsSlot).enrichedTags),
    async compute(calls, text) {
      return { enrichedTags: (await tagsAndBasics(calls, text)).tags };
    },
  },
  // The My Vibe basics tiles. `fields` is an object, not an array, so its freshness check
  // can't use the array-shaped test the other slots take.
  basics: {
    isFilled: (r) => !!r && typeof (r as BasicsSlot).fields === 'object' && (r as BasicsSlot).fields !== null,
    async compute(calls, text) {
      return { fields: (await tagsAndBasics(calls, text)).basics };
    },
  },
  // The Profile Builder chat's OPENING questions — a bank of 10 generated once per profile
  // "version", from which each drawer open serves a rotating window of 3. Openers depend on
  // nothing but the profile text, which is what makes them safe to cache; follow-ups are the
  // opposite and are deliberately NOT pooled.
  starterPool: {
    isFilled: (r) => Array.isArray((r as StarterPoolSlot).questions),
    async compute(calls, text) {
      return { questions: await starterQuestionPoolFromAI(calls, text) };
    },
  },
};

export function profileDerivedIsFresh(
  slot: SlotName,
  rec: SlotRecord | null | undefined,
  text: string,
): boolean {
  if (!rec || !rec.computedAt || !SLOTS[slot].isFilled(rec)) return false;
  // EXACT text identity, deliberately — a slot is fresh only for the profile it was
  // computed from. There used to be a PROFILE_FILTER_REFRESH_WORDS tolerance here (any edit
  // moving the word count by < 10 counted as a touch-up and kept the stored values), which
  // meant a synthesis pass could rewrite what the student actually said while the tags,
  // subjects and basics went on describing the previous version. Worse, it was a word-COUNT
  // test, not a content test: swapping "robotics" for "marine biology" is a zero-word delta
  // and left every derived value wrong indefinitely.
  //
  // The trade is real and is accepted: every synthesis now invalidates all four slots, so a
  // merge costs their rebuild (~5 background model calls) instead of sometimes skipping it.
  // Page visits are unaffected — unchanged text still matches exactly and is still served
  // from cache — and the rebuild is fire-and-forget off the critical path.
  return rec.profile === text;
}

// slot -> { text, promise } for a computation already running, so a background refresh
// started after a profile merge and a search landing mid-flight share one call.
const inFlight: Partial<Record<SlotName, { text: string; promise: Promise<SlotRecord> }>> = {};

// Slot writes are SERIALIZED, because each one is a read-modify-write of the whole profile
// record: /api/data/save replaces the value at a key wholesale, so a slot write has to load
// the record, add its own key, and save it back. Two slots finishing within one load
// round-trip of each other would then both read the pre-write record, and the second save
// would silently drop the first slot's values.
//
// refreshProfileDerived fires all four slots at once, which is exactly that window — and
// since freshness became exact-text identity it fires on EVERY synthesis, so this is a
// routine interleaving rather than a rare one. Only the persist step queues; the model calls
// still run concurrently, so wall time is unchanged.
let slotWriteQueue: Promise<unknown> = Promise.resolve();
function queueSlotWrite<T>(write: () => Promise<T>): Promise<T> {
  // .then(write, write) so one failed write cannot wedge the queue for the others.
  const next = slotWriteQueue.then(write, write);
  slotWriteQueue = next.catch(() => undefined);
  return next;
}

// Returns the stored values when they were computed from exactly this profile text,
// otherwise computes and persists them now — so a profile that predates this cache, or one
// edited while storage was unavailable, still works; it just pays once.
export async function getProfileDerived(
  store: ProfileStore,
  calls: ModelCalls,
  slot: SlotName,
  record?: ProfileRecord | null,
): Promise<SlotRecord> {
  const rec = record ?? (await store.load());
  const text = rec?.synthesized || '';
  const stored = rec?.[slot] as SlotRecord | undefined;
  if (profileDerivedIsFresh(slot, stored, text)) return stored as SlotRecord;

  const flight = inFlight[slot];
  if (flight && flight.text === text) return flight.promise;

  const promise = (async () => {
    try {
      const computed: SlotRecord = {
        ...(await SLOTS[slot].compute(calls, text)),
        profile: text,
        wordCount: countProfileWords(text),
        computedAt: new Date().toISOString(),
      };
      // The profile can be edited while this is in flight; don't overwrite values for text
      // that's already been superseded — the next call recomputes against the new text.
      // Re-read rather than reusing `rec`, so a concurrent slot write isn't clobbered, and
      // queue the whole load+save so no other slot can land between the two halves.
      await queueSlotWrite(async () => {
        const latest = (await store.load()) ?? {};
        if ((latest.synthesized || '') === text) {
          await store.save({ ...latest, [slot]: computed });
        }
      });
      return computed;
    } finally {
      if (inFlight[slot] && inFlight[slot]!.text === text) delete inFlight[slot];
    }
  })();
  inFlight[slot] = { text, promise };
  return promise;
}

// The one way search flows should read subjects + grade.
export async function getProfileFilterValues(
  store: ProfileStore,
  calls: ModelCalls,
  record?: ProfileRecord | null,
): Promise<{ subjects: string[]; grade: number | null }> {
  const rec = (await getProfileDerived(store, calls, 'filterValues', record)) as FilterValuesSlot;
  return { subjects: rec.subjects || [], grade: rec.grade ?? null };
}

// Synchronous read of a stored slot, for callers that must not block (the results filter bar
// paints from this before deciding whether anything is missing). Returns null — distinct
// from [] — when nothing has been computed for the current text yet.
export function cachedProfileFilterTags(record: ProfileRecord | null | undefined): EnrichedTag[] | null {
  const rec = record?.filterTags;
  if (!profileDerivedIsFresh('filterTags', rec, record?.synthesized || '')) return null;
  return rec!.enrichedTags;
}

// Fire-and-forget refresh after a profile edit, so neither a search nor a results render has
// to pay for these. All slots go at once — they don't block each other here. A failure is not
// user-facing: the next reader recomputes, or does without (no subject hints for the
// pre-filter, no tag facet on the bar).
export function refreshProfileDerived(
  store: ProfileStore,
  calls: ModelCalls,
  record?: ProfileRecord | null,
): void {
  if (!record?.synthesized) return;
  (Object.keys(SLOTS) as SlotName[]).forEach((slot) => {
    getProfileDerived(store, calls, slot, record).catch((err) =>
      console.warn(`Profile ${slot} refresh failed:`, (err as Error).message),
    );
  });
}

// Exported for the tests/verification harness.
export const ALL_SLOTS = Object.keys(SLOTS) as SlotName[];
