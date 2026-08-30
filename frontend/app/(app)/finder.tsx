import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { addTrackerItemChecked, flattenItems, loadTrackerData } from '@/api/trackerStore';
import type { MatchFunnelOption, MatchResponse, MatchStudentBlob, Opportunity } from '@/api/types';
import { PROFILE_SUFFICIENT_LENGTH } from '@/lib/constants';
import { ACTIVE_KINDS, KIND_CONFIG } from '@/lib/kinds';
import { countProfileWords, extractHighlightProjects } from '@/lib/profile';
import { type EnrichedTag } from '@/lib/profileTags';
import {
  cachedProfileFilterTags,
  getProfileDerived,
  refreshProfileDerived,
  type BasicsSlot,
  type FilterTagsSlot,
  type ModelCalls,
  type ProfileRecord,
  type ProfileStore,
} from '@/lib/profileDerived';
import { parseGradeFromText, parseGradeLevel } from '@/lib/grade';
import { extractJSON } from '@/lib/extractJSON';
import { preFilter, rankCandidates, type RankedPick } from '@/lib/ranking';
import { markNewlyAdded } from '@/lib/newlyAdded';
import { awaitProfileWrites } from '@/lib/profileWrites';
import {
  extractTrackerInfo,
  findBucketForKind,
  normalizeVerifiedActionItems,
  staticGenericChecklist,
  type TrackerInfo,
} from '@/lib/tracker';
import { MiniBadge, PopButton, ReviewBadge, Screen, SoftCard, Txt, usePopInteraction } from '@/ui/components';
import {
  NotInterestedModal, ReviewDrawer, RungStep, ShortlistView,
  type ShortlistItem, type Tier,
} from '@/features/freshFinds/ShortlistView';
import { colors, fonts, popShadow, radius, space } from '@/ui/theme';

interface Result {
  opp: Opportunity;
  reason: string;
  tier: 'strong' | 'look';
  // The server marked this a stretch/exploration pick (MatchResultCard.exploration_pick) — the
  // curated shortlist renders it as a distinct "Stretch pick" tier. Absent on browse/form results.
  exploration?: boolean;
  // Which kind's ranking call surfaced this. Set by the profile-driven fan-out; absent on a
  // single-kind form search. Preferred over deriving a kind from opp.type, which only ever
  // guessed at what the search actually did.
  kind?: string;
}
const callGemini = httpClient.callGemini.bind(httpClient);
// The profile-derived slots need both providers and a place to persist to. Defined once at
// module scope: they hold no state, so a new object per render would only defeat the
// in-flight de-duplication inside getProfileDerived.
const modelCalls: ModelCalls = {
  gemini: callGemini,
  claude: httpClient.callClaude.bind(httpClient),
};
const profileStore: ProfileStore = {
  load: () => httpClient.loadData<ProfileRecord>('student-profile'),
  save: (record) => httpClient.saveData('student-profile', record),
};
type Stage = 'home' | 'form' | 'funnel' | 'results';

// Quiet retries before the catalog failure is shown to the student. Two is enough to ride
// out a cold backend or a dropped connection without leaving them staring at a spinner.
const CATALOG_RETRIES = 2;
const CATALOG_RETRY_DELAY_MS = 800;

// Map a catalog opportunity's `type` to a kind key (used when adding from a mixed suggest list).
// Every type the catalog actually carries must appear here: an unmapped type falls through
// to 'summer' and files the opportunity in the Quest Log as a summer program, which is how
// volunteer roles and the lone `Academic` row ended up labelled camps.
function kindForOpp(opp: Opportunity): string {
  const map: Record<string, string> = {
    Program: 'summer', Internship: 'internship', Conference: 'conference',
    Journal: 'journal', Research: 'research-competition', Competition: 'pure-competition',
    Volunteer: 'volunteer', Academic: 'pure-competition',
  };
  return map[(opp.type as string) ?? ''] ?? 'summer';
}

// The taxonomy quiz (QUIZ_ROOT/QUIZ_SUB, Direction E) was RETIRED in Phase 6 — the
// progressive funnel replaced its "help me figure out what fits" job. Students who know the
// kind they want still use the browse grid; everyone else uses "Suggest for me" (the funnel).

const FILTER_FIELDS = [
  { key: 'type', label: 'Type' },
  { key: 'price', label: 'Cost' },
  { key: 'season', label: 'Season' },
  { key: 'location', label: 'Format' },
] as const;
type FilterKey = (typeof FILTER_FIELDS)[number]['key'];

// Plenty of catalog rows carry no cost, season or format. The facet list was built from
// non-empty values only, so those rows could not satisfy ANY checked option and silently
// disappeared the moment a student touched a filter. They now get an explicit option they
// can see and choose, rather than being quietly excluded.
const BLANK_FACET = '__unspecified__';
const BLANK_FACET_LABEL = 'Not specified';
function facetValue(opp: Opportunity, key: FilterKey): string {
  const v = opp[key];
  return typeof v === 'string' && v.trim() ? v : BLANK_FACET;
}

// The "Your Profile" facet's enriched tags, stored on the shared student-profile record
// (PROFILE_DERIVED_SLOTS.filterTags). EnrichedTag and the generator now live in
// src/lib/profileTags.ts — this screen both READS the slot and, when it is missing or
// stale, computes and persists it. Until that writer existed the facet worked only for
// accounts carrying tags from the retired SPA.
interface TagScore {
  reasoning?: string;
  rank: number;
}

// ---------- Session-scoped result cache ----------
// The authed shell renders a <Slot/>, so navigating away UNMOUNTS this screen. That used to
// discard the results and re-run two paid AI calls on the next visit, which meant the same
// unchanged profile produced a different list every time the student opened the tab, with
// nothing on screen explaining why.
//
// Deliberately a module singleton and NOT persisted: it survives tab-switching and dies on
// reload. It is keyed on the profile text it was searched from, so deepening the profile
// invalidates it and the next visit genuinely re-searches — a cached list must never
// outlive the thing it claims to be based on.
interface SessionSearch {
  profileKey: string;
  results: Result[];
  suggestMode: boolean;
  kind: string;
  note: string | null;
  // Tag scores ride along with the results they were computed against, so restoring a
  // cached list also restores the work done on top of it.
  tagScores: Map<string, Record<string, TagScore>>;
}
let sessionSearch: SessionSearch | null = null;

// batchScoreOpportunitiesWithAI, ported: one Gemini call scoring the visible results
// against the selected tag; returns null on failure (distinct from "nothing matched").
async function scoreOpportunitiesForTag(tag: EnrichedTag, opps: Opportunity[]): Promise<Record<string, TagScore> | null> {
  const oppsList = opps
    .map((o) => `ID: ${o.id} | Name: ${o.name} | Type: ${o.type} | Summary: ${o.summary || '(no description)'}`)
    .join('\n');
  const system = `You are helping a student find opportunities that match their interests and goals. Write directly to them in second person (using "you").`;
  const userContent = `STUDENT'S PROFILE TAG: "${tag.tag}"
INTENT: ${tag.intent || '(no intent specified)'}
NEXT STEPS: ${(tag.nextSteps || []).join(', ') || '(no specific steps)'}

OPPORTUNITIES TO RANK:
${oppsList}

Rank these opportunities by relevance to this student's profile. Return JSON array with only genuinely relevant opportunities:
[
  { "id": "opp_id", "rank": 1, "reasoning": "Brief 1-sentence message directly to the student using 'you' language" },
  ...
]

For each reasoning, write directly to the student as if you're the app speaking to them. Omit opportunities that don't align with the profile. Include only good/strong matches.
Return ONLY valid JSON, no markdown, no preamble.`;
  try {
    const raw = await callGemini(system, userContent, false);
    const results = extractJSON(raw);
    if (!Array.isArray(results)) return null;
    const scores: Record<string, TagScore> = {};
    results.forEach((r: { id?: string; rank?: number; reasoning?: string }) => {
      if (r && r.id) scores[r.id] = { reasoning: r.reasoning, rank: r.rank ?? 999 };
    });
    return scores;
  } catch {
    return null;
  }
}

// Local keyword fallback when the scoring call fails (opportunityMatchesProfileTag, ported).
function tagKeywordMatch(opp: Opportunity, tag: string): boolean {
  const oppText = `${opp.name} ${opp.org ?? ''} ${opp.summary ?? ''}`.toLowerCase();
  const tagLower = tag.toLowerCase();
  if (oppText.includes(tagLower)) return true;
  const stop = new Set(['and', 'the', 'for', 'with', 'from', 'that', 'this', 'are', 'was', 'using', 'app', 'project', 'students', 'investigating', 'current']);
  const words = tagLower.split(/\s+/).filter((w) => w.length > 2 && !stop.has(w));
  if (!words.length) return false;
  const hits = words.filter((w) => oppText.includes(w));
  return hits.length >= Math.min(2, Math.max(1, Math.ceil(words.length / 2)));
}

export default function Finder() {
  const router = useRouter();
  const [opps, setOpps] = useState<Opportunity[] | null>(null);
  const [oppsError, setOppsError] = useState<string | null>(null);
  const [oppsLoading, setOppsLoading] = useState(true);
  // Mount flag shared by every async path here, so a retry loop that outlives the screen
  // can't setState on an unmounted component.
  const aliveRef = useRef(true);
  // The last-loaded student-profile record, so the slot readers don't re-fetch it per call.
  const profileRecord = useRef<ProfileRecord | null>(null);
  const [profileText, setProfileText] = useState('');
  // "Your profile is empty" is also what an unloaded profile looks like, so the hero flashed
  // that on every visit before the fetch landed. Gate it on the load actually resolving.
  const [profileLoaded, setProfileLoaded] = useState(false);
  // Come back to the tab and you land back ON the list, not on a hero telling you it
  // exists. The results survived the unmount (sessionSearch); making the student press
  // "View my matches" to see them again is a step that only exists because of how this
  // screen is built.
  const [stage, setStage] = useState<Stage>(() => (sessionSearch?.results.length ? 'results' : 'home'));
  const [browseOpen, setBrowseOpen] = useState(false);
  const [kind, setKind] = useState<string>(() => sessionSearch?.kind ?? ACTIVE_KINDS[0]);
  const [suggestMode, setSuggestMode] = useState(() => sessionSearch?.suggestMode ?? false);

  const [description, setDescription] = useState('');
  const [grade, setGrade] = useState('');
  const [homeState, setHomeState] = useState('');
  const [freeOnly, setFreeOnly] = useState(false);
  const [remote, setRemote] = useState(false);

  const [searching, setSearching] = useState(false);
  // Seeded from the session cache so tab-switching returns the same list rather than
  // re-running the search. Validated against the profile once it loads (see below).
  const [results, setResults] = useState<Result[]>(() => sessionSearch?.results ?? []);
  const [note, setNote] = useState<string | null>(() => sessionSearch?.note ?? null);
  // ---- Phase 4 progressive funnel ----
  // The student blob is built once (rung 0) and reused for every rung. answers + the current
  // surviving pool_ids live in refs (mutated as the student advances); history drives Back.
  const funnelBlob = useRef<MatchStudentBlob | null>(null);
  const funnelAnswers = useRef<Record<string, string>>({});
  const funnelPoolIds = useRef<string[] | null>(null);
  const [funnelRung, setFunnelRung] = useState<MatchResponse | null>(null);
  const [funnelLoading, setFunnelLoading] = useState(false);
  const [funnelHistory, setFunnelHistory] = useState<
    { answers: Record<string, string>; poolIds: string[] | null; rung: MatchResponse }[]
  >([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [trackedIds, setTrackedIds] = useState<Set<string>>(new Set());
  // Curated-shortlist (suggest path) UI state, ported from the opportunity-matching branch:
  // not-interested dismissals (a client-only "show me fewer like this" — nothing is deleted),
  // the review-your-answers drawer, and the running record of funnel answers that drawer shows.
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(new Set());
  const [dismissTargetId, setDismissTargetId] = useState<string | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [funnelReview, setFunnelReview] = useState<{ label: string; value: string }[]>([]);
  // Hover-lift for result cards (.pop-card:hover in the live app) — one shared id rather
  // than a hook per card, since the card list is rendered via .map(), not its own component.
  const [hoveredCardId, setHoveredCardId] = useState<string | null>(null);
  // Which card's review popover is open. One id for the whole list, so opening a second one
  // closes the first — the rule toggleReviewInfo() enforced in the retired SPA.
  const [openReviewId, setOpenReviewId] = useState<string | null>(null);
  const [hoveredSaveBtnId, setHoveredSaveBtnId] = useState<string | null>(null);
  const [pressedSaveBtnId, setPressedSaveBtnId] = useState<string | null>(null);
  const [hoveredFacetKey, setHoveredFacetKey] = useState<string | null>(null);
  const [pressedFacetKey, setPressedFacetKey] = useState<string | null>(null);
  const profileFacetPop = usePopInteraction(2, colors.slate900, 1);
  const [adding, setAdding] = useState(false);
  const [addProgress, setAddProgress] = useState<{ done: number; total: number } | null>(null);
  const [visibleCount, setVisibleCount] = useState(10);
  const [untrackedOnly, setUntrackedOnly] = useState(false);
  const [filters, setFilters] = useState<Record<FilterKey, Set<string>>>({ type: new Set(), price: new Set(), season: new Set(), location: new Set() });
  const [openFacet, setOpenFacet] = useState<FilterKey | 'profile' | null>(null);
  // A facet dropdown is absolutely positioned under its toggle with a fixed width. On a
  // phone the filter row wraps, so a toggle can sit far enough right that a left-anchored
  // panel runs off the screen (the "Type" filter did). When a facet opens we measure its
  // toggle against the viewport and flip the panel to right-anchored if a left-anchored one
  // would overflow — so it opens leftward from the toggle and stays on screen.
  //
  // Measurement is done here (on web, via the toggle's DOM rect at open time) rather than
  // with onLayout: onLayout proved not to fire reliably for these wrapped flex children, and
  // the viewport is what the panel must fit inside anyway — not the filter bar.
  const facetToggleRefs = useRef<Record<string, unknown>>({});
  const [facetAlign, setFacetAlign] = useState<Record<string, 'left' | 'right'>>({});
  const toggleFacet = (key: FilterKey | 'profile', panelW: number) => {
    if (openFacet === key) { setOpenFacet(null); return; }
    let align: 'left' | 'right' = 'left';
    if (Platform.OS === 'web') {
      const node = facetToggleRefs.current[key] as { getBoundingClientRect?: () => DOMRect } | null;
      const rect = node?.getBoundingClientRect?.();
      if (rect && typeof window !== 'undefined') {
        const margin = 8;
        // Flip to right only if a left-anchored panel would run past the right edge AND a
        // right-anchored one actually fits (its left edge stays on screen).
        if (rect.left + panelW > window.innerWidth - margin && rect.right - panelW >= margin) align = 'right';
      }
    }
    setFacetAlign((a) => ({ ...a, [key]: align }));
    setOpenFacet(key);
  };
  const [profileTags, setProfileTags] = useState<EnrichedTag[]>([]);
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [tagScores, setTagScores] = useState<Record<string, TagScore> | null>(null);
  const [tagScoring, setTagScoring] = useState(false);
  const tagScoreCache = useRef(sessionSearch?.tagScores ?? new Map<string, Record<string, TagScore>>());
  // True while the tag slot is being generated for a profile that has none yet.
  const [tagsBuilding, setTagsBuilding] = useState(false);

  // Load the catalog, retrying transient failures quietly before admitting defeat. Every
  // entry point on this screen is dead without it, so a failure has to become something the
  // student can SEE and act on — it used to be recorded and never rendered, which left
  // "Suggest opportunities for me" as a button that did nothing at all.
  const loadOpportunities = useCallback(async (attempt = 0): Promise<void> => {
    setOppsError(null);
    setOppsLoading(true);
    try {
      // Discontinued programs never reach the results, the browse list, or the ranker —
      // filtered at the source so no path can miss it. The test is `=== 'not_running'`, never
      // `!== 'running'`: the catalog's `status` is NULL on 1195 of its 1239 active rows (never
      // deadline-checked), and reading that absence as "not running" would empty Fresh Finds.
      const r = await httpClient.getOpportunities();
      if (!aliveRef.current) return;
      setOpps(r.filter((o) => o.status !== 'not_running'));
    } catch (e) {
      if (!aliveRef.current) return;
      if (attempt < CATALOG_RETRIES) {
        await new Promise((res) => setTimeout(res, CATALOG_RETRY_DELAY_MS * (attempt + 1)));
        if (!aliveRef.current) return;
        return loadOpportunities(attempt + 1);
      }
      setOppsError((e as Error).message);
    } finally {
      if (aliveRef.current) setOppsLoading(false);
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    void loadOpportunities();
    // Wait for any profile rewrite still running on My Vibe BEFORE reading the profile.
    // Without this the finder read whatever was stored at that instant — still the previous
    // profile — matched its session cache against that stale text and showed the old list,
    // then the synthesis landed and the list changed underneath the student. The catalog
    // fetch above deliberately does not wait: it is independent of the profile, so it can
    // get on with the slower request while we hold here.
    awaitProfileWrites()
      .then(() => (aliveRef.current ? httpClient.loadData<ProfileRecord>('student-profile') : null))
      .then((p) => {
        if (!aliveRef.current) return;
        const text = p?.synthesized ?? '';
        setProfileText(text);
        // A cached list must never outlive the profile it was searched from. If the student
        // deepened their story on another tab, drop it and let the auto-run search again.
        if (sessionSearch && sessionSearch.profileKey !== text) {
          sessionSearch = null;
          tagScoreCache.current = new Map();
          setResults([]);
          setNote(null);
          // We restored straight onto the results stage from the cache; with the cache gone
          // there is nothing to show there, so fall back to the hero rather than an empty page.
          setStage('home');
        }
        profileRecord.current = p ?? null;
        // Paint from the stored slot immediately where it is still fresh (never blocking on
        // a model call to draw a filter bar), then warm every slot in the background so the
        // next search, the tag facet, the basics tiles and the chat openers are all served
        // from cache. cachedProfileFilterTags returns null — distinct from [] — when nothing
        // has been computed for the current text yet.
        const cachedTags = cachedProfileFilterTags(p);
        if (cachedTags) setProfileTags(cachedTags.filter((t) => t && typeof t.tag === 'string'));
        if (countProfileWords(text) >= PROFILE_SUFFICIENT_LENGTH) {
          if (!cachedTags) setTagsBuilding(true);
          refreshProfileDerived(profileStore, modelCalls, p);
          // Re-read the tags once the refresh lands. A failure leaves the facet hidden.
          void getProfileDerivedTags(p);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (aliveRef.current) setProfileLoaded(true);
      });
    loadTrackerData()
      .then((d) => aliveRef.current && setTrackedIds(new Set(flattenItems(d).map((i) => i.id))))
      .catch(() => {});
    return () => {
      aliveRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Read the tag slot through the shared cache, so this shares one in-flight call with the
  // background warm above rather than paying for a second. Never throws: the facet is an
  // enhancement on top of results already on screen, so a failure just hides the dropdown.
  async function getProfileDerivedTags(record: ProfileRecord | null) {
    try {
      const slot = (await getProfileDerived(profileStore, modelCalls, 'filterTags', record)) as FilterTagsSlot;
      if (!aliveRef.current) return;
      setProfileTags((slot.enrichedTags || []).filter((t) => t && typeof t.tag === 'string'));
    } catch (e) {
      console.warn('Profile tag build failed; the tag facet stays hidden:', (e as Error).message);
    } finally {
      if (aliveRef.current) setTagsBuilding(false);
    }
  }

  // Score the current result set against the selected profile tag (cached per tag+ids,
  // like the old tagScoreCache — toggling filters or saving cards never re-pays the call).
  useEffect(() => {
    if (!selectedTag || !results.length) {
      setTagScores(null);
      return;
    }
    const tag = profileTags.find((t) => t.tag === selectedTag);
    if (!tag) return;
    const key = selectedTag + '::' + results.map((r) => r.opp.id).join(',');
    const hit = tagScoreCache.current.get(key);
    if (hit) {
      setTagScores(hit);
      return;
    }
    let alive = true;
    setTagScoring(true);
    scoreOpportunitiesForTag(tag, results.map((r) => r.opp))
      .then((scores) => {
        if (!alive) return;
        if (scores) {
          tagScoreCache.current.set(key, scores);
          setTagScores(scores);
        } else {
          setTagScores(null); // fall back to the keyword matcher below
        }
      })
      .finally(() => alive && setTagScoring(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTag, results]);

  const profileReady = countProfileWords(profileText) >= PROFILE_SUFFICIENT_LENGTH;

  // Auto-run the profile-based suggestion once when entering with a ready profile.
  const autoRan = useRef(false);
  // Whether the auto-run decision has been MADE yet — distinct from whether it ran. Until
  // both the catalog and the profile have landed we cannot know if a search is about to
  // start, and rendering the idle hero in that gap makes it blink to the spinner a frame
  // later. A ref can't drive this: the hero has to re-render when it settles.
  const [autoRunSettled, setAutoRunSettled] = useState(!!sessionSearch?.results.length);
  useEffect(() => {
    if (autoRan.current) return;
    if (!opps || !profileLoaded) return; // still deciding — keep showing the loading state
    autoRan.current = true;
    if (profileReady && stage === 'home' && !results.length) void suggestForMe();
    setAutoRunSettled(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opps, profileLoaded, profileReady]);

  // The single "Finding your matches…" state: profile loading, catalog loading, the gap
  // before the auto-run starts, and the search itself. Not shown once the catalog has
  // failed — that has its own card with a retry, and a spinner would sit there forever.
  const booting = !oppsError && (!profileLoaded || !autoRunSettled || searching);

  // The ONLY copy this screen shows while it is working. Declared once because two branches
  // render it: the boot/search state, and the "ready profile, no results yet" case that used
  // to be an idle "Fresh Finds" hero with its own Suggest button. From the student's side
  // both are the same thing — the tab is getting their matches — so both say the same thing.
  //
  // Nothing can park here indefinitely: the auto-run fires as soon as the catalog and
  // profile land, and a search that FAILS routes to the results stage (see search()'s catch)
  // where the error and a way forward are actually visible, rather than falling back here.
  const loadingHero = (
    <LoadingRow
      title="Finding your matches…"
      sub="Searching based on everything in your profile."
    />
  );

  function openForm(k: string) {
    setKind(k);
    setSuggestMode(false);
    setDescription('');
    setResults([]);
    setNote(null);
    setStage('form');
  }

  function buildPrefs(): string {
    const parts: string[] = [];
    if (freeOnly) parts.push('free or low-cost only');
    if (remote) parts.push('remote-friendly');
    if (homeState.trim()) parts.push(`based in or near ${homeState.trim()}`);
    return parts.join(', ');
  }

  async function search(desc: string, k: string | null, prefs: string) {
    if (!opps || !desc.trim() || searching) return;
    setSearching(true);
    setNote(null);
    const cfg = k ? KIND_CONFIG[k] : null;
    const strict = !!cfg?.strictType;
    try {
      // Grade for the FORM path: the dropdown wins when set; otherwise fall back to the
      // profile's LLM-extracted basics grade (the resolved source of truth since Phase 6 —
      // the old regex `filterValues` slot + the 17-bucket subject nudge are both retired, so
      // the form path is now keyword + type + grade, and the LLM ranker does the fit work).
      let profileGrade: number | null = null;
      try {
        const basics = (await getProfileDerived(profileStore, modelCalls, 'basics', profileRecord.current)) as BasicsSlot;
        profileGrade = parseGradeLevel(basics.fields?.grade ?? null);
      } catch {
        /* best effort — no profile grade just means the dropdown (or none) decides */
      }
      const gradeNum = parseGradeFromText(grade) ?? profileGrade;

      // ---- The profile-driven path is the server-side curated match via the FUNNEL ----
      // (OPPORTUNITY_MATCHING_PLAN.md Phases 3+4.) Rung 0 runs recall server-side and either
      // asks the first discriminating question (-> the funnel stage) or, if nothing is worth
      // asking, returns the curated <=10 directly (-> results). Later rungs are driven by
      // answerFunnel/skipFunnel/funnelBack, not by search(). The 7-kind client fan-out this
      // replaced returned 40-70 rows to wade through.
      if (!k) {
        funnelBlob.current = await buildStudentBlob();
        funnelAnswers.current = {};
        funnelPoolIds.current = null;
        setFunnelHistory([]);
        setFunnelRung(null);
        await runFunnelStep({}, null);
        return;
      }

      const { pool, typeMatches, widened, strictEmpty } = preFilter(
        opps, desc, cfg?.dbTypes ?? null, strict, gradeNum,
      );
      // A strict kind with nothing of its type in the catalog. Say so and stop BEFORE the
      // paid ranking call — there is nothing here to rank, and widening would have handed
      // back a page of the wrong kind of opportunity.
      if (strictEmpty) {
        setResults([]);
        setNote(`We don't have any ${(cfg?.name ?? 'opportunities of this type').toLowerCase()} listings in the catalog right now. Try another type, or check back soon.`);
        rememberSearch([], `We don't have any ${(cfg?.name ?? 'opportunities of this type').toLowerCase()} listings in the catalog right now. Try another type, or check back soon.`, k);
        setSelected(new Set());
        setVisibleCount(10);
        setStage('results');
        return;
      }
      // The type filter was dropped because too few rows carried it. The student asked for
      // one kind of thing and is about to be shown others — tell them, rather than letting
      // it look like the app ignored the choice they just made.
      const widenNote = widened
        ? typeMatches === 0
          ? `We don't have any ${(cfg?.name ?? 'matching').toLowerCase()} listings yet, so these are other opportunity types that fit what you described.`
          : `Only ${typeMatches} ${(cfg?.name ?? 'matching').toLowerCase()} listing${typeMatches === 1 ? '' : 's'} matched what you described, so we've included other opportunity types below.`
        : null;
      const byId = new Map(pool.map((o) => [o.id, o]));
      try {
        // rankCandidates already retries one parse failure internally; add one more full
        // attempt after a short backoff so a transient rate-limit/truncation doesn't drop
        // the student to the keyword fallback.
        let ranked: RankedPick[];
        try {
          ranked = await rankCandidates(callGemini, desc, pool, prefs || null, strict);
        } catch (err) {
          console.warn('rankCandidates failed once, retrying after backoff:', (err as Error).message);
          await new Promise((r) => setTimeout(r, 1500));
          ranked = await rankCandidates(callGemini, desc, pool, prefs || null, strict);
        }
        const mapped = ranked
          .map((r) => (byId.get(r.id) ? { opp: byId.get(r.id) as Opportunity, reason: r.reason, tier: r.tier } : null))
          .filter((x): x is Result => x !== null);
        if (!mapped.length) throw new Error('AI ranking returned no usable matches');
        setResults(mapped);
        setNote(widenNote);
        rememberSearch(mapped, widenNote, k);
      } catch (err) {
        console.error('AI ranking unavailable, falling back to keyword order:', (err as Error).message);
        const fallback = pool.slice(0, 12).map((opp) => ({ opp, reason: '', tier: 'look' as const }));
        // Both facts matter and neither may hide the other: the ranking is degraded AND the
        // type filter may have been dropped.
        const fallbackNote = [widenNote, 'Showing keyword matches — AI ranking is unavailable right now.']
          .filter(Boolean).join(' ');
        setNote(fallbackNote);
        setResults(fallback);
        rememberSearch(fallback, fallbackNote, k);
      }
      setSelected(new Set());
      setVisibleCount(10);
      setStage('results');
    } catch (e) {
      // Land on the results stage even though there are none. The note and the "No matches
      // this time" card live there, so a failure is something the student can SEE and act
      // on; staying on the home stage left the message rendered nowhere, and now that the
      // idle hero is gone it would leave a spinner running over a search that had stopped.
      const msg = `Search failed: ${(e as Error).message}`;
      setNote(msg);
      setResults([]);
      rememberSearch([], msg, k);
      setStage('results');
    } finally {
      setSearching(false);
    }
  }

  // Persist a finished search into the session cache so returning to this tab shows the
  // same list instead of paying for a fresh one. Keyed on the profile text it was based on.
  function rememberSearch(list: Result[], noteText: string | null, k: string | null) {
    sessionSearch = {
      profileKey: profileText,
      results: list,
      suggestMode: k === null,
      kind: k ?? kind,
      note: noteText,
      tagScores: tagScoreCache.current,
    };
  }

  // The Phase-2 student blob, assembled from the profile (used for every funnel rung and the
  // final curation). Grade + location prefer the LLM basics slot, falling back to the regex
  // filter value / form field; themes come from the filterTags slot; projects from the text.
  async function buildStudentBlob(): Promise<MatchStudentBlob> {
    let themes: { theme: string; intent?: string | null; next_steps?: string | null }[] = [];
    try {
      const slot = (await getProfileDerived(profileStore, modelCalls, 'filterTags', profileRecord.current)) as FilterTagsSlot;
      themes = (slot.enrichedTags || [])
        .filter((t) => t && typeof t.tag === 'string')
        .map((t) => ({ theme: t.tag, intent: t.intent ?? null, next_steps: (t.nextSteps || []).join('; ') || null }));
    } catch {
      /* thin profile -> no themes */
    }
    let studentGrade: number | null = null;
    let studentState: string | null = homeState.trim() || null;
    try {
      const basics = (await getProfileDerived(profileStore, modelCalls, 'basics', profileRecord.current)) as BasicsSlot;
      const bg = parseGradeLevel(basics.fields?.grade ?? null);
      if (bg != null) studentGrade = bg;
      if (basics.fields?.state) studentState = basics.fields.state;
    } catch { /* best effort */ }
    return {
      grade: studentGrade,
      location: { state: studentState },
      profile_themes: themes,
      highlight_projects: extractHighlightProjects(profileText),
    };
  }

  // Map a curated /api/match response onto the loaded catalog rows and land on the results
  // stage — the shared tail of both the funnel's "done" and the (mock) direct path.
  function finishFunnelToResults(resp: MatchResponse) {
    const byId = new Map((opps ?? []).map((o) => [o.id, o]));
    const merged: Result[] = (resp.results ?? [])
      .map((r): Result | null => {
        const opp = byId.get(r.id);
        if (!opp) return null;
        return {
          opp,
          reason: r.reason || '',
          tier: (r.tier === 'strong' ? 'strong' : 'look') as 'strong' | 'look',
          exploration: !!r.exploration_pick,
          kind: kindForOpp(opp),
        };
      })
      .filter((x): x is Result => x !== null);
    setResults(merged);
    setNote(resp.note ?? null);
    rememberSearch(merged, resp.note ?? null, null);
    setSelected(new Set());
    setVisibleCount(10);
    setStage('results');
  }

  // One funnel round trip: POST the current answers + narrowed pool; either show the next
  // question or, when the server says done, the curated list. Recall only runs on rung 0
  // (no pool_ids); later rungs carry the client-narrowed pool_ids so nothing re-embeds.
  async function runFunnelStep(answers: Record<string, string>, poolIds: string[] | null, curateNow = false) {
    setFunnelLoading(true);
    try {
      const resp = await httpClient.match({
        ...(funnelBlob.current ?? {}),
        funnel: true,
        funnel_answers: answers,
        pool_ids: poolIds ?? undefined,
        curate_now: curateNow || undefined,
      });
      if (resp.done !== false) {
        finishFunnelToResults(resp);
        return;
      }
      funnelAnswers.current = answers;
      funnelPoolIds.current = resp.pool_ids ?? poolIds ?? null;
      setFunnelRung(resp);
      setStage('funnel');
    } catch (e) {
      const msg = `Search failed: ${(e as Error).message}`;
      setNote(msg);
      setResults([]);
      rememberSearch([], msg, null);
      setStage('results');
    } finally {
      setFunnelLoading(false);
    }
  }

  // Student picked an option: narrow the pool locally (keep every id not "cut" under that
  // option — the guard already ran server-side), record the answer, advance.
  function answerFunnel(opt: MatchFunnelOption) {
    const rung = funnelRung;
    if (!rung || !rung.axis) return;
    const cls = rung.classification || {};
    const currentPool = rung.pool_ids || funnelPoolIds.current || [];
    const narrowed = currentPool.filter((id) => (cls[id]?.per_option?.[opt.value] ?? 'keep') !== 'cut');
    setFunnelHistory((h) => [...h, { answers: funnelAnswers.current, poolIds: funnelPoolIds.current, rung }]);
    void runFunnelStep({ ...funnelAnswers.current, [rung.axis]: opt.value }, narrowed);
  }

  // Skip: record the axis as answered (so the server doesn't re-ask it) but keep the pool.
  function skipFunnel() {
    const rung = funnelRung;
    if (!rung || !rung.axis) return;
    setFunnelHistory((h) => [...h, { answers: funnelAnswers.current, poolIds: funnelPoolIds.current, rung }]);
    void runFunnelStep({ ...funnelAnswers.current, [rung.axis]: '__skip__' }, funnelPoolIds.current);
  }

  // Back: restore the previous rung without a round trip.
  function funnelBack() {
    setFunnelHistory((h) => {
      if (!h.length) return h;
      const prev = h[h.length - 1];
      funnelAnswers.current = prev.answers;
      funnelPoolIds.current = prev.poolIds;
      setFunnelRung(prev.rung);
      setStage('funnel');
      return h.slice(0, -1);
    });
  }

  // ---- Curated-shortlist wiring (the funnel + shortlist re-skin) ----
  // Short, friendly labels for the review drawer, keyed on the rung axis the server returned.
  const AXIS_LABEL: Record<string, string> = {
    cost: 'Budget', time_commitment: 'Time', citizenship: 'Citizenship', hard_demographic: 'Eligibility',
    type: 'Type', season: 'Timing', format: 'Format', subject: 'Subject',
    // Behavioral vibe axes (rerank-only) — labelled "Vibe" in the review drawer.
    selectivity: 'Vibe', residential: 'Vibe', collaboration: 'Vibe',
    structure: 'Vibe', intensity: 'Vibe', output: 'Vibe',
  };
  // A rung is a "vibe" question (rerank-only, never filters) when the server says so — Phase D
  // adds these server-side; until then no rung is a vibe rung and this stays false.
  function rungIsVibe(rung: MatchResponse | null): boolean {
    return !!rung && (rung as { kind?: string }).kind === 'vibe';
  }
  function rungPick(value: string) {
    const rung = funnelRung;
    if (!rung || !rung.axis) return;
    const opt = (rung.options ?? []).find((o) => o.value === value);
    if (!opt) return;
    setFunnelReview((r) => [...r, { label: AXIS_LABEL[rung.axis!] ?? rung.axis!, value: opt.label }]);
    answerFunnel(opt);
  }
  function rungSkip() {
    const rung = funnelRung;
    if (rung?.axis) setFunnelReview((r) => [...r, { label: AXIS_LABEL[rung.axis!] ?? rung.axis!, value: 'Skipped' }]);
    skipFunnel();
  }
  function rungBack() {
    setFunnelReview((r) => r.slice(0, -1));
    funnelBack();
  }
  // "Show my matches now": curate the pool narrowed so far, skipping the remaining questions.
  function showAllNow() {
    const rung = funnelRung;
    const poolIds = rung?.pool_ids || funnelPoolIds.current || null;
    void runFunnelStep(funnelAnswers.current, poolIds, true);
  }
  function buildReviewSections(): { title: string; items: { label: string; value: string }[] }[] {
    const secs: { title: string; items: { label: string; value: string }[] }[] = [];
    const blob = funnelBlob.current;
    const about: { label: string; value: string }[] = [];
    if (blob?.grade != null) about.push({ label: 'Grade', value: `${blob.grade}th grade` });
    if (blob?.location?.state) about.push({ label: 'Location', value: String(blob.location.state) });
    if (about.length) secs.push({ title: 'About you', items: about });
    if (funnelReview.length) secs.push({ title: 'This search', items: funnelReview });
    return secs;
  }
  // Start the funnel over from scratch, clearing the session cache and every per-run choice.
  function restartFunnel() {
    setFunnelReview([]);
    setDismissedIds(new Set());
    setReviewOpen(false);
    setSelected(new Set());
    sessionSearch = null;
    void suggestForMe();
  }

  async function suggestForMe() {
    setSuggestMode(true);
    await search(profileText, null, buildPrefs());
  }

  // P8 (collapsed producer): the add is now three INDEPENDENT sources, each authoritative
  // for its own slice, replacing the old full extractTrackerInfo() web-search pass that
  // re-derived everything the two Claude endpoints already produce verified.
  //   meta/fit  — the slim Gemini call (descriptive only, no dates, no search)
  //   dates/status/note — the shared, cached deadline endpoint (the ONLY date producer now;
  //               G4 is moot — there is no client date guess left for a verified-empty
  //               result to wipe)
  //   tasks     — the verified action-items endpoint, else the static generic checklist
  //   apply link — the catalog's own link-checked opp.url
  // Each source failing degrades only its slice, so a Gemini outage no longer reduces the
  // whole add to a database-only stub.
  // Returns what actually happened, so the caller can stop claiming an add that the store
  // refused. The Quest Log rejects an item whose id OR url is already tracked, and this used
  // to swallow that — the card flipped to "In Quest Log", the batch was badged NEW, and
  // nothing had been written.
  async function addOneToTracker(
    opp: Opportunity,
    reason: string,
    resultKind?: string,
  ): Promise<{ added: boolean; existingName?: string }> {
    // Same precedence as the card's category badge: the kind that actually surfaced this
    // beats a guess derived from opp.type, so the Quest Log files it where it was found.
    const bucket = findBucketForKind(resultKind ?? (suggestMode ? kindForOpp(opp) : kind));
    const url = (opp.url as string) ?? null;
    const type = (opp.type as string) ?? null;
    const reviewStatus = (opp.review_status as string) ?? null;
    const reviewSummary = (opp.review_summary as string) ?? null;
    const summary = (opp.summary as string) || '';

    let slim: { meta?: string; fit?: string } = {};
    try {
      try {
        slim = await extractTrackerInfo(callGemini, opp);
      } catch (firstErr) {
        console.warn(`Retrying ${opp.name} after error:`, (firstErr as Error).message);
        slim = await extractTrackerInfo(callGemini, opp);
      }
    } catch (err) {
      console.warn(`meta/fit extraction failed for ${opp.name}:`, (err as Error).message);
    }

    let deadline: Partial<TrackerInfo> | null = null;
    try {
      deadline = await httpClient.getDeadlineCheck(opp.id);
    } catch (err) {
      console.warn(`Deadline check failed for ${opp.name}:`, (err as Error).message);
    }

    // The catalog's checklist, generated and quote-verified server-side (getActionItems
    // never throws — null on failure). The static generic list is the fallback when the
    // endpoint has nothing — it asserts nothing, so it cannot reintroduce the
    // invented-prerequisite failure the old model fallback carried.
    const shared = await httpClient.getActionItems(opp.id);
    const verified = normalizeVerifiedActionItems(shared?.action_items, opp.id);
    const sharedItems = verified.length ? verified : staticGenericChecklist(opp.id, url);

    const status = deadline?.status
      && ['running', 'not_running', 'rolling', 'unknown'].includes(deadline.status)
      ? deadline.status
      : 'unknown';
    const res = await addTrackerItemChecked(bucket, {
      id: opp.id,
      name: opp.name,
      url,
      type,
      bucket,
      progressStatus: 'not_started',
      status,
      reviewStatus,
      reviewSummary,
      meta: slim.meta || [opp.org, opp.type, opp.price, opp.location].filter(Boolean).join(' · '),
      fit: slim.fit || reason || summary,
      note: deadline?.important_date_note
        || (deadline
          ? 'Details from the opportunities database — confirm on the official site.'
          : "Live details couldn't be fetched — showing database info only. Check the official site directly."),
      noteType: status === 'not_running' ? 'flag' : deadline ? 'plain' : 'flag',
      importantDates: Array.isArray(deadline?.important_dates)
        ? deadline.important_dates
            .filter((d) => d && d.date_iso)
            .map((d) => ({
              label: d.label || 'Date',
              dateISO: d.date_iso,
              type: d.type || 'deadline',
              estimated: d.estimated,
              verified: d.verified,
              sourceUrl: d.source_url ?? null,
            }))
            .sort((a, b) => a.dateISO.localeCompare(b.dateISO))
        : [],
      deadlineLabel: 'CHECK SITE',
      wasEstimated: !!deadline?.was_estimated,
      applyUrl: url,
      applyLabel: 'Apply / learn more',
      actionItems: sharedItems,
    });
    return { added: res.added, existingName: res.existing?.name };
  }

  async function addSelectedToTracker() {
    if (!selected.size || adding) return;
    setAdding(true);
    const ids = [...selected];
    setAddProgress({ done: 0, total: ids.length });
    try {
      // Only ids the store ACTUALLY wrote get marked tracked and badged NEW. A duplicate
      // (same id or same url as something already tracked) is reported by name instead of
      // being silently dropped behind a "In Quest Log" label.
      const addedIds: string[] = [];
      const duplicates: string[] = [];
      for (let i = 0; i < ids.length; i++) {
        const r = results.find((x) => x.opp.id === ids[i]);
        if (r) {
          const outcome = await addOneToTracker(r.opp, r.reason, r.kind);
          if (outcome.added) addedIds.push(ids[i]);
          else duplicates.push(outcome.existingName || r.opp.name);
        }
        setAddProgress({ done: i + 1, total: ids.length });
      }
      // Only this batch carries the NEW treatment in the Quest Log — see markNewlyAdded.
      markNewlyAdded(addedIds);
      setTrackedIds((p) => new Set([...p, ...addedIds]));
      setSelected(new Set());
      if (duplicates.length) {
        const names = duplicates.slice(0, 3).join(', ');
        const more = duplicates.length > 3 ? ` and ${duplicates.length - 3} more` : '';
        setNote(
          addedIds.length
            ? `Added ${addedIds.length}. Already in your Quest Log: ${names}${more}.`
            : `Already in your Quest Log: ${names}${more}. Nothing new to add.`,
        );
      }
      // Adding is the point of departure to the Quest Log — land there instead of leaving
      // the student on a Fresh Finds page that now just shows the same cards as "tracked".
      // Nothing added means nothing to go and look at, so stay put and show the reason.
      if (addedIds.length) router.push('/(app)/tracker');
    } catch (e) {
      setNote(`Couldn't add: ${(e as Error).message}`);
    } finally {
      setAdding(false);
      setAddProgress(null);
    }
  }

  function toggleSelect(id: string) {
    setSelected((p) => {
      const n = new Set(p);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }
  // Everything the Clear control undoes, counted the same way it clears — the field facets,
  // the profile tag, and the untracked toggle. Keep the two in step or the count lies.
  const activeFilterCount =
    FILTER_FIELDS.reduce((n, f) => n + filters[f.key].size, 0) +
    (selectedTag ? 1 : 0) +
    (untrackedOnly ? 1 : 0);

  function clearAllFilters() {
    setFilters({ type: new Set(), price: new Set(), season: new Set(), location: new Set() });
    setSelectedTag(null);
    setUntrackedOnly(false);
    setVisibleCount(10);
  }
  function toggleFilter(key: FilterKey, value: string) {
    setFilters((p) => {
      const n = { ...p, [key]: new Set(p[key]) };
      if (n[key].has(value)) n[key].delete(value);
      else n[key].add(value);
      return n;
    });
    setVisibleCount(10);
  }

  // Tracked first, then saved (selected), then tier (script.js renderResults).
  const sortedResults = useMemo(() => {
    const rank = (r: Result) => (trackedIds.has(r.opp.id) ? 0 : selected.has(r.opp.id) ? 1 : 2);
    const tierOrder = { strong: 0, look: 1 };
    return [...results].sort((a, b) => {
      const d = rank(a) - rank(b);
      if (d !== 0) return d;
      return tierOrder[a.tier] - tierOrder[b.tier];
    });
  }, [results, trackedIds, selected]);

  // filterResultList, ported: field facets → profile-tag filter (AI scores when they
  // resolved, keyword fallback otherwise) → untracked filter.
  const filteredResults = useMemo(() => {
    let filtered = sortedResults.filter((r) => {
      for (const f of FILTER_FIELDS) {
        const set = filters[f.key];
        if (set.size && !set.has(facetValue(r.opp, f.key))) return false;
      }
      return true;
    });
    if (selectedTag) {
      if (tagScores) {
        filtered = filtered
          .filter((r) => tagScores[r.opp.id])
          .map((r) => ({ ...r, aiReasoning: tagScores[r.opp.id].reasoning, aiRank: tagScores[r.opp.id].rank }))
          .sort((a, b) => (a.aiRank ?? 999) - (b.aiRank ?? 999));
      } else if (!tagScoring) {
        filtered = filtered.filter((r) => tagKeywordMatch(r.opp, selectedTag));
      }
    }
    if (untrackedOnly) filtered = filtered.filter((r) => !trackedIds.has(r.opp.id));
    return filtered as (Result & { aiReasoning?: string; aiRank?: number })[];
  }, [sortedResults, untrackedOnly, filters, trackedIds, selectedTag, tagScores, tagScoring]);
  const visibleResults = filteredResults.slice(0, visibleCount);

  // ---------- Home stage ----------
  if (stage === 'home') {
    return (
      <Screen>
        {/* The catalog failed to load after its quiet retries. Every path on this screen
            needs it, so this replaces the hero rather than sitting beside a CTA that
            cannot work. */}
        {!!oppsError && (
          <SoftCard style={styles.heroCard}>
            <Text style={styles.heroTitle}>We couldn't load the opportunities</Text>
            <Text style={[styles.heroSub, styles.heroSubItalic]}>
              This is on our side, not yours — your profile and Quest Log are safe. {oppsError}
            </Text>
            <PopButton
              label={oppsLoading ? 'Retrying…' : 'Try again'}
              loading={oppsLoading}
              onPress={() => void loadOpportunities()}
              style={styles.selfStart}
            />
          </SoftCard>
        )}
        {!oppsError && (
        <SoftCard style={styles.heroCard}>
          {/* One loading state, not two. Fetching the profile is setup the student never
              asked for and shouldn't have to watch — from their side the tab is doing one
              thing, so it says one thing. The state still has to exist (it is what stops
              "Your profile is empty" flashing before the profile has loaded) and it still
              covers the gap before the auto-run starts, or the default hero would blink
              between the two. */}
          {booting ? (
            loadingHero
          ) : !profileText ? (
            <>
              <Text style={styles.heroTitle}>Your profile is empty</Text>
              <Text style={[styles.heroSub, styles.heroSubItalic]}>
                Every match here gets better once we know you. Takes 2 minutes — add a few things and your matches will show up right here.
              </Text>
              <PopButton label="Build my profile" onPress={() => router.push('/(app)/profile')} style={styles.selfStart} />
            </>
          ) : !profileReady ? (
            <>
              <Text style={styles.heroTitle}>I don't have enough yet to match opportunities</Text>
              <Text style={[styles.heroSub, styles.heroSubItalic]}>Help me help you by building your profile</Text>
              <PopButton label="Deepen your story" onPress={() => router.push('/(app)/profile')} style={styles.selfStart} />
            </>
          ) : results.length ? (
            <>
              <Text style={styles.heroTitle}>Your matches are ready</Text>
              {/* These are the matches from earlier this session, not a fresh search — say
                  so, and offer the re-run explicitly rather than doing it unasked. */}
              <Text style={[styles.heroSub, styles.heroSubItalic]}>Based on everything in your profile.</Text>
              <View style={styles.heroActions}>
                <PopButton label="View my matches →" onPress={() => setStage('results')} />
                <Pressable onPress={() => { sessionSearch = null; void suggestForMe(); }}>
                  <Text style={styles.link}>Search again</Text>
                </Pressable>
              </View>
            </>
          ) : (
            loadingHero
          )}
        </SoftCard>
        )}

        {/* Held results must be reachable no matter which hero branch is showing. The hero
            tests the PROFILE first, so a student who searched by browsing without a profile
            saw "Your profile is empty" with their results stranded behind it. */}
        {!oppsError && !!results.length && !profileReady && (
          <Pressable style={styles.centerLink} onPress={() => setStage('results')}>
            <Text style={styles.link}>← Back to your {results.length} match{results.length === 1 ? '' : 'es'}</Text>
          </Pressable>
        )}

        <Pressable style={styles.centerLink} onPress={() => setBrowseOpen((b) => !b)}>
          <Text style={styles.link}>{browseOpen ? 'Hide opportunity types' : 'Click here to browse opportunities'}</Text>
        </Pressable>

        {browseOpen && (
          <SoftCard style={{ gap: 24, padding: 32 }}>
            <Txt variant="h2">What kind of opportunity are you looking for?</Txt>
            <View style={styles.grid}>
              {ACTIVE_KINDS.map((k) => (
                <Pressable key={k} style={styles.kindCard} onPress={() => openForm(k)}>
                  <Text style={styles.kindName}>{KIND_CONFIG[k].name}</Text>
                  <Text style={styles.kindDesc}>{KIND_CONFIG[k].desc}</Text>
                </Pressable>
              ))}
            </View>
          </SoftCard>
        )}
      </Screen>
    );
  }

  // ---------- Form stage ----------
  if (stage === 'form') {
    const cfg = KIND_CONFIG[kind];
    return (
      <Screen>
        <BackLink label="Back to opportunity type" onPress={() => setStage('home')} />
        <SoftCard style={{ gap: 16, padding: 32 }}>
          <Txt variant="h2" style={{ marginBottom: 8 }}>{cfg.heading}</Txt>

          <View style={{ gap: 8 }}>
            <Text style={styles.fieldLabel}>{cfg.label.toUpperCase()}</Text>
            <Pressable>
              <TextArea value={description} onChangeText={setDescription} placeholder={cfg.placeholder} />
            </Pressable>
            <Text style={styles.charCount}>{description.length} characters - aim for at least 200</Text>
          </View>

          <View style={styles.formRow}>
            <View style={styles.flex1}>
              <Text style={styles.fieldLabelMuted}>GRADE LEVEL (OPTIONAL)</Text>
              <SoftSelect value={grade || 'Prefer not to say'} options={['Prefer not to say', 'Middle School', '9th grade', '10th grade', '11th grade', '12th grade']} onChange={(v) => setGrade(v === 'Prefer not to say' ? '' : v)} />
            </View>
            <View style={styles.flex1}>
              <Text style={styles.fieldLabelMuted}>HOME STATE (OPTIONAL)</Text>
              <SoftInput value={homeState} onChangeText={setHomeState} placeholder="e.g. Washington" />
            </View>
          </View>

          <View style={styles.formRow}>
            <View style={styles.flex1}>
              <Text style={styles.fieldLabelMuted}>COST PREFERENCE</Text>
              <SoftSelect value={freeOnly ? 'Free only' : 'No preference'} options={['No preference', 'Free only']} onChange={(v) => setFreeOnly(v === 'Free only')} />
            </View>
            <View style={styles.flex1}>
              <Text style={styles.fieldLabelMuted}>FORMAT PREFERENCE</Text>
              <SoftSelect value={remote ? 'Remote-friendly' : 'No preference'} options={['No preference', 'Remote-friendly']} onChange={(v) => setRemote(v === 'Remote-friendly')} />
            </View>
          </View>

          {!!note && <Text style={styles.note}>{note}</Text>}
          <View style={styles.formActions}>
            <PopButton
              label="Find matching opportunities"
              variant="secondary"
              square
              loading={searching}
              disabled={!opps || !description.trim()}
              onPress={() => search(description, kind, buildPrefs())}
              style={styles.findBtn}
              textStyle={styles.findBtnText}
            />
          </View>
        </SoftCard>
      </Screen>
    );
  }

  // ---------- Funnel stage (Phase 4) ----------
  // One discriminating question at a time, narrowing the pool toward the curated shortlist.
  // Each option shows how many matches it would LEAVE (the live counter), which doubles as the
  // T3 "relax" affordance — a student who sees an option leaves too few just picks a broader
  // one or skips. Answering/skipping advances; Back restores the previous question.
  if (stage === 'funnel' && funnelRung) {
    const opts = funnelRung.options ?? [];
    const isVibe = rungIsVibe(funnelRung);
    // Live pool count for the header: the largest surviving-option count is the size of the
    // pool the student is choosing within (skipping keeps all of it).
    const poolCount = isVibe
      ? null
      : opts.reduce((m, o) => (typeof o.count === 'number' && o.count > m ? o.count : m), 0) || null;
    return (
      <RungStep
        question={funnelRung.question || 'Which fits you best?'}
        rationale={funnelRung.rationale}
        options={opts.map((o) => ({ label: o.label, value: o.value, count: o.count }))}
        isVibe={isVibe}
        poolCount={poolCount}
        canBack={funnelHistory.length > 0}
        loading={funnelLoading}
        onPick={rungPick}
        onSkip={rungSkip}
        onBack={rungBack}
        onShowAll={showAllNow}
      />
    );
  }

  // ---------- Curated shortlist (the suggest/funnel path) ----------
  // The polished shortlist re-skin, driven by the server-curated `results` (tier + why-it-fits).
  // The legacy grid + facets below is kept only for the browse/form path (suggestMode === false).
  if (suggestMode) {
    const picks: ShortlistItem[] = results.map((r) => ({
      opp: r.opp,
      reason: r.reason,
      tier: (r.exploration ? 'stretch' : r.tier) as Tier,
      flags: [],
    }));
    const dismissName = dismissTargetId
      ? (results.find((r) => r.opp.id === dismissTargetId)?.opp.name ?? 'this')
      : 'this';
    return (
      <>
        <ShortlistView
          picks={picks}
          pendingIds={selected}
          savedIds={trackedIds}
          dismissedIds={dismissedIds}
          onToggle={toggleSelect}
          onNotInterested={(id) => setDismissTargetId(id)}
          onSubmit={addSelectedToTracker}
          onStartFresh={restartFunnel}
          onReview={() => setReviewOpen(true)}
        />
        {dismissTargetId && (
          <NotInterestedModal
            name={dismissName}
            onPick={() => {
              setDismissedIds((d) => new Set(d).add(dismissTargetId));
              setSelected((p) => { const n = new Set(p); n.delete(dismissTargetId); return n; });
              setDismissTargetId(null);
            }}
            onClose={() => setDismissTargetId(null)}
          />
        )}
        <ReviewDrawer
          open={reviewOpen}
          onClose={() => setReviewOpen(false)}
          sections={buildReviewSections()}
          onAdjust={() => { setReviewOpen(false); restartFunnel(); }}
        />
      </>
    );
  }

  // ---------- Results stage ----------
  // The selection bar must live OUTSIDE the Screen's ScrollView: position:fixed doesn't
  // hold inside RN-web's scroll container, so the bar is an absolute sibling instead
  // (matching the old app's sticky-bottom behavior).
  return (
    <View style={styles.resultsWrap}>
    <Screen contentStyle={{ paddingBottom: 90 }}>
      {/* Deepen story banner */}
      <LinearGradient colors={[colors.bannerFrom, colors.bannerTo]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={styles.deepenBanner}>
        <View style={styles.flex1}>
          <Text style={styles.deepenTitle}>Want more matches like these?</Text>
          <Text style={styles.deepenSub}>Deepen your story by adding more details.</Text>
        </View>
        <View style={styles.deepenRight}>
          <Pressable style={styles.deepenBtn} onPress={() => router.push('/(app)/profile')}>
            <Text style={styles.deepenBtnText}>Deepen your story</Text>
          </Pressable>
          <Pressable onPress={() => { setStage('home'); setBrowseOpen(true); }}>
            <Text style={styles.deepenAlt}>or browse opportunities</Text>
          </Pressable>
        </View>
      </LinearGradient>

      {/* An open facet panel closes when the student presses anywhere else. Rendered as a
          full-bleed transparent backdrop UNDER the panel (zIndex below facetPanel, above
          the page) so a press that lands outside is caught here, while presses inside the
          panel never reach it. Cross-platform: no document-level listener, works on native.
          Note the panels sit inside the filter bar's stacking context, so this also has to
          out-rank the bar itself — hence filterBackdrop's zIndex sits between them. */}
      {openFacet !== null && (
        <Pressable
          style={styles.filterBackdrop}
          onPress={() => setOpenFacet(null)}
          accessibilityLabel="Close filter menu"
        />
      )}

      {/* Filter row */}
      <View style={styles.filterBar}>
        <Text style={styles.filterLabel}>FILTER:</Text>
        {profileTags.length > 0 && (
          <View ref={(r) => { facetToggleRefs.current.profile = r; }}>
            <Pressable
              {...profileFacetPop.handlers}
              style={[styles.filterToggle, profileFacetPop.shadowStyle]}
              onPress={() => toggleFacet('profile', 320)}
            >
              <Text style={styles.filterToggleText}>▾ Your Profile{selectedTag ? ' (1)' : ''}</Text>
            </Pressable>
            {openFacet === 'profile' && (
              /* Scrolls, because the tag list has no fixed length: it is as long as the
                 profile is broad. This panel is absolutely positioned, so an over-long list
                 simply ran off the bottom of the viewport and the tags below the fold could
                 not be reached at all. `None` sits outside the scroller so the way to clear
                 the filter is always visible, never scrolled past. */
              <View style={[styles.facetPanel, styles.facetPanelWide, facetAlign.profile === 'right' ? styles.facetPanelRight : styles.facetPanelLeft]}>
                <Pressable style={styles.facetRow} onPress={() => { setSelectedTag(null); setVisibleCount(10); setOpenFacet(null); }}>
                  <Text style={styles.facetRowText}>{selectedTag ? '○' : '●'} None</Text>
                </Pressable>
                <ScrollView style={styles.facetScroll} nestedScrollEnabled>
                  {profileTags.map((t) => (
                    <Pressable key={t.tag} style={styles.facetRow} onPress={() => { setSelectedTag(t.tag); setVisibleCount(10); setOpenFacet(null); }}>
                      <Text style={styles.facetRowText}>{selectedTag === t.tag ? '●' : '○'} {t.tag}</Text>
                    </Pressable>
                  ))}
                </ScrollView>
              </View>
            )}
          </View>
        )}
        <Pressable style={[styles.filterToggle, untrackedOnly && styles.filterToggleOn]} onPress={() => setUntrackedOnly(!untrackedOnly)}>
          <Text style={styles.filterToggleText}>{untrackedOnly ? '☑' : '☐'} Only untracked</Text>
        </Pressable>
        {FILTER_FIELDS.map((f) => {
          // Real values sorted alphabetically, with "Not specified" pinned LAST — it is an
          // absence, not a peer of the real options, and sorting it among them invites
          // reading it as one.
          const raw = [...new Set(sortedResults.map((r) => facetValue(r.opp, f.key)))];
          const values = [
            ...raw.filter((v) => v !== BLANK_FACET).sort(),
            ...(raw.includes(BLANK_FACET) ? [BLANK_FACET] : []),
          ];
          if (values.length < 2) return null;
          const active = filters[f.key].size;
          return (
            <View key={f.key} ref={(r) => { facetToggleRefs.current[f.key] = r; }}>
              <Pressable
                onHoverIn={() => setHoveredFacetKey(f.key)}
                onHoverOut={() => setHoveredFacetKey((cur) => (cur === f.key ? null : cur))}
                onPressIn={() => setPressedFacetKey(f.key)}
                onPressOut={() => setPressedFacetKey((cur) => (cur === f.key ? null : cur))}
                style={[
                  styles.filterToggle,
                  popShadow(pressedFacetKey === f.key ? 1 : hoveredFacetKey === f.key ? 3 : 2, colors.slate900),
                  pressedFacetKey === f.key
                    ? styles.filterTogglePressed
                    : hoveredFacetKey === f.key && styles.filterToggleHovered,
                ]}
                onPress={() => toggleFacet(f.key, 224)}
              >
                <Text style={styles.filterToggleText}>▾ {f.label}{active ? ` (${active})` : ''}</Text>
              </Pressable>
              {openFacet === f.key && (
                <View style={[styles.facetPanel, facetAlign[f.key] === 'right' ? styles.facetPanelRight : styles.facetPanelLeft]}>
                  {values.map((v) => (
                    <Pressable key={v} style={styles.facetRow} onPress={() => toggleFilter(f.key, v)}>
                      <Text style={styles.facetRowText}>
                        {filters[f.key].has(v) ? '☑' : '☐'} {v === BLANK_FACET ? BLANK_FACET_LABEL : v}
                      </Text>
                    </Pressable>
                  ))}
                </View>
              )}
            </View>
          );
        })}
        {/* Only shown once something is actually filtering. A permanently-visible Clear is
            noise, and its absence was the only way out of a filter combination that hid
            everything short of un-ticking each box by hand. */}
        {activeFilterCount > 0 && (
          <Pressable style={styles.clearFilters} onPress={clearAllFilters}>
            <Text style={styles.clearFiltersText}>✕ Clear filters ({activeFilterCount})</Text>
          </Pressable>
        )}
      </View>
      {!!note && <Text style={styles.note}>{note}</Text>}
      {tagScoring && <LoadingRow title="Scoring matches against your profile…" inline />}
      {tagsBuilding && <LoadingRow title="Building your profile filters…" inline />}

      {/* Empty states. These are two genuinely different situations and must not share a
          message: "the search found nothing" is about the search, "your filters hid
          everything" is about a control the student can undo right here. Before this the
          page simply rendered nothing at all, which reads as broken rather than empty. */}
      {!results.length && (
        <SoftCard style={styles.emptyCard}>
          <Text style={styles.heroTitle}>No matches this time</Text>
          <Text style={[styles.heroSub, styles.heroSubItalic]}>
            {suggestMode
              ? "We couldn't find opportunities that fit what's in your profile yet. Adding more detail gives us far more to work with."
              : "Nothing in the catalog lined up with what you described. Try describing it differently, or browse by type."}
          </Text>
          <View style={styles.heroActions}>
            <PopButton label="Deepen your story" onPress={() => router.push('/(app)/profile')} />
            <Pressable onPress={() => { setStage('home'); setBrowseOpen(true); }}>
              <Text style={styles.link}>Browse all opportunity types</Text>
            </Pressable>
          </View>
        </SoftCard>
      )}
      {!!results.length && !filteredResults.length && (
        <SoftCard style={styles.emptyCard}>
          <Text style={styles.heroTitle}>Your filters hid everything</Text>
          <Text style={[styles.heroSub, styles.heroSubItalic]}>
            {results.length} match{results.length === 1 ? '' : 'es'} {results.length === 1 ? 'is' : 'are'} waiting behind the filters above.
          </Text>
          <PopButton label="Clear all filters" onPress={clearAllFilters} style={styles.selfStart} />
        </SoftCard>
      )}

      {/* Result cards */}
      {visibleResults.map(({ opp, reason, tier, kind: resultKind, aiReasoning, aiRank }) => {
        const isSelected = selected.has(opp.id);
        const isTracked = trackedIds.has(opp.id);
        // Prefer the kind whose ranking call actually surfaced this card. kindForOpp only
        // ever guessed from opp.type, and guessed wrong for any row a widened pool returned.
        const cat = resultKind
          ? (KIND_CONFIG[resultKind]?.name ?? 'Opportunity')
          : suggestMode
            ? (KIND_CONFIG[kindForOpp(opp)]?.name ?? 'Opportunity')
            : KIND_CONFIG[kind].name;
        const reviewOpen = openReviewId === opp.id;
        const metaPills = [opp.org, opp.type, opp.price, opp.location, opp.state && opp.state !== 'All States' ? opp.state : null, opp.season]
          .filter((x): x is string => typeof x === 'string' && x.trim().length > 0);
        const cardHovered = hoveredCardId === opp.id;
        return (
          <Pressable
            key={opp.id}
            onHoverIn={() => setHoveredCardId(opp.id)}
            onHoverOut={() => setHoveredCardId((cur) => (cur === opp.id ? null : cur))}
            style={[
              styles.resultCard,
              popShadow(cardHovered ? 6 : 4),
              cardHovered && styles.resultCardHovered,
              isSelected && styles.resultCardSelected,
              // Cards are siblings in source order, so without this the popover on card N is
              // painted over by card N+1 instead of overlapping it.
              reviewOpen && styles.resultCardReviewOpen,
            ]}
          >
            <View style={styles.cardTopRow}>
              <View style={styles.badgeRow}>
                <MiniBadge label={cat} bg={colors.violet200} fg={colors.violet900} />
                {tier === 'strong' ? (
                  <MiniBadge label="⭐ Strong Fit" bg={colors.yellow300} fg={colors.slate900} />
                ) : (
                  <MiniBadge label="Worth a look" bg={colors.slate100} fg={colors.slate900} />
                )}
                <ReviewBadge
                  status={opp.review_status as string | null | undefined}
                  summary={opp.review_summary as string | null | undefined}
                  open={reviewOpen}
                  onToggle={() => setOpenReviewId((cur) => (cur === opp.id ? null : opp.id))}
                />
              </View>
              {isTracked ? (
                <Pressable style={styles.trackedTag} onPress={() => router.push('/(app)/tracker')}>
                  <Text style={styles.trackedTagText}>📌 In Quest Log. Make edits there.</Text>
                </Pressable>
              ) : (
                <Pressable
                  onHoverIn={() => setHoveredSaveBtnId(opp.id)}
                  onHoverOut={() => setHoveredSaveBtnId((cur) => (cur === opp.id ? null : cur))}
                  onPressIn={() => setPressedSaveBtnId(opp.id)}
                  onPressOut={() => setPressedSaveBtnId((cur) => (cur === opp.id ? null : cur))}
                  style={[
                    styles.saveBtn,
                    popShadow(pressedSaveBtnId === opp.id ? 1 : hoveredSaveBtnId === opp.id ? 4 : 3),
                    pressedSaveBtnId === opp.id
                      ? styles.saveBtnPressed
                      : hoveredSaveBtnId === opp.id && styles.saveBtnHovered,
                    isSelected && styles.saveBtnSelected,
                  ]}
                  onPress={() => toggleSelect(opp.id)}
                >
                  <Text style={styles.saveBtnText}>{isSelected ? '⭐ Saved Match' : '⭐ Save Match'}</Text>
                </Pressable>
              )}
            </View>

            <Pressable onPress={() => opp.url && Linking.openURL(opp.url as string)}>
              <Text style={styles.resultName}>{opp.name}</Text>
            </Pressable>

            {aiReasoning ? (
              <View style={styles.whyRow}>
                <View style={[styles.whyBar, styles.whyBarIndigo]} />
                <View style={styles.flex1}>
                  <Text style={styles.whyLabel}>PROFILE MATCH{aiRank ? ` • RANK #${aiRank}` : ''}</Text>
                  <Text style={styles.whyText}>{aiReasoning}</Text>
                </View>
              </View>
            ) : reason ? (
              <View style={styles.whyRow}>
                <View style={styles.whyBar} />
                <View style={styles.flex1}>
                  <Text style={styles.whyLabel}>WHY IT FITS</Text>
                  <Text style={styles.whyText}>{reason}</Text>
                </View>
              </View>
            ) : null}

            {metaPills.length > 0 && (
              <View style={styles.metaRow}>
                {metaPills.map((p, i) => (
                  <View key={i} style={styles.metaPill}>
                    <Text style={styles.metaPillText}>{p}</Text>
                  </View>
                ))}
              </View>
            )}
            {!!opp.summary && (
              <Text style={styles.summary} numberOfLines={3}>{opp.summary as string}</Text>
            )}
          </Pressable>
        );
      })}

      {filteredResults.length > visibleCount && (
        <View style={styles.centerLink}>
          <PopButton label={`Show more (${filteredResults.length - visibleCount} left)`} variant="ink" small square shadowColor={colors.slate900} onPress={() => setVisibleCount((c) => c + 10)} />
        </View>
      )}

    </Screen>

      {/* Selection bar — absolute sibling of the scroller so it stays pinned to the viewport bottom. */}
      {results.length > 0 && (
        <View style={styles.selectionBar}>
          <Text style={styles.selectionCount}>{selected.size} selected</Text>
          <PopButton
            label={addProgress ? `Fetching details (${addProgress.done}/${addProgress.total})…` : adding ? 'Adding…' : 'Add to my tracker →'}
            loading={adding}
            disabled={!selected.size}
            onPress={addSelectedToTracker}
          />
        </View>
      )}
    </View>
  );
}

// The one "working" indicator this screen uses. Every progress state renders through it, so
// the hero spinner and the in-results progress lines cannot drift apart in style. `inline`
// is for the ones that sit in the results scroll rather than inside the hero card: they need
// their own vertical breathing room, which the card already supplies.
function LoadingRow({ title, sub, inline }: { title: string; sub?: string; inline?: boolean }) {
  return (
    <View style={[styles.loadingRow, inline && styles.loadingRowInline]}>
      <ActivityIndicator color={colors.orangeDeep} size="small" />
      <View style={styles.flex1}>
        <Text style={[styles.heroTitleSm, inline && styles.loadingRowInlineTitle]}>{title}</Text>
        {!!sub && <Text style={styles.heroSub}>{sub}</Text>}
      </View>
    </View>
  );
}

// ---------- Small soft form controls (lavender, Poppins-ish) ----------
function TextArea({ value, onChangeText, placeholder }: { value: string; onChangeText: (t: string) => void; placeholder?: string }) {
  return (
    <TextInput
      value={value}
      onChangeText={onChangeText}
      placeholder={placeholder}
      placeholderTextColor={colors.muted}
      multiline
      style={styles.textArea}
    />
  );
}
function SoftInput({ value, onChangeText, placeholder }: { value: string; onChangeText: (t: string) => void; placeholder?: string }) {
  return (
    <TextInput
      value={value}
      onChangeText={onChangeText}
      placeholder={placeholder}
      placeholderTextColor={colors.muted}
      style={styles.softInput}
    />
  );
}
function SoftSelect({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <View>
      <Pressable style={styles.softInput} onPress={() => setOpen(!open)}>
        <Text style={styles.softSelectText}>{value}  ▾</Text>
      </Pressable>
      {open && (
        <View style={[styles.facetPanel, styles.facetPanelLeft]}>
          {options.map((o) => (
            <Pressable key={o} style={styles.facetRow} onPress={() => { onChange(o); setOpen(false); }}>
              <Text style={styles.facetRowText}>{o}</Text>
            </Pressable>
          ))}
        </View>
      )}
    </View>
  );
}

function BackLink({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable style={styles.back} onPress={onPress}>
      <Text style={styles.backText}>← {label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  flex1: { flex: 1, minWidth: 0 },
  selfStart: { alignSelf: 'flex-start', marginTop: 8 },
  centerLink: { alignItems: 'center' },
  // A primary action with a quieter alternative beside it (View matches / Search again,
  // Deepen your story / Browse all).
  heroActions: { flexDirection: 'row', alignItems: 'center', gap: 16, marginTop: 8, flexWrap: 'wrap' },
  emptyCard: { padding: 32, gap: 8, alignItems: 'flex-start' },
  link: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.muted, textDecorationLine: 'underline' },

  heroCard: { padding: 40, gap: 8 },
  loadingRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  loadingRowInline: { paddingVertical: 8 },
  // Smaller than the hero's, because in the results list this sits above the cards rather
  // than being the only thing on screen.
  loadingRowInlineTitle: { fontSize: 16, lineHeight: 22 },
  heroTitle: { fontFamily: fonts.display, fontSize: 30, lineHeight: 38, color: colors.navy, maxWidth: 576 },
  heroTitleSm: { fontFamily: fonts.display, fontSize: 24, lineHeight: 30, color: colors.navy },
  heroSub: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.inkSoft, marginTop: 4 },
  heroSubItalic: { fontStyle: 'italic', fontSize: 16, lineHeight: 26, maxWidth: 576 },

  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 16 },
  kindCard: { flexGrow: 1, flexBasis: '46%', minWidth: 150, backgroundColor: colors.lavender, borderRadius: radius.lg, padding: 16, gap: 4 },
  kindName: { fontFamily: fonts.display, fontSize: 16, color: colors.ink },
  kindDesc: { fontFamily: fonts.bodyMed, fontSize: 13, color: colors.inkSoft, marginTop: 4 },
  quizCta: { borderWidth: 2, borderColor: colors.slate400, borderStyle: 'dashed', borderRadius: radius.lg, paddingVertical: 16, alignItems: 'center' },
  quizCtaText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.slate500 },

  quizQuestion: { fontFamily: fonts.display, fontSize: 18, color: colors.ink },
  quizOption: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.navy, borderRadius: radius.md, padding: 16, gap: 2 },
  quizOptionHovered: { transform: [{ translateX: -1 }, { translateY: -1 }] },
  quizOptionPressed: { transform: [{ translateX: 2 }, { translateY: 2 }] },
  quizOptTitle: { fontFamily: fonts.display, fontSize: 18, color: colors.navy },
  quizOptDesc: { fontFamily: fonts.bodyMed, fontSize: 14, color: colors.inkSoft },

  fieldLabel: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500, letterSpacing: 0.6, textTransform: 'uppercase' },
  fieldLabelMuted: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.muted, letterSpacing: 0.6, textTransform: 'uppercase', marginBottom: 8 },
  textArea: { backgroundColor: colors.lavender, borderRadius: radius.lg, padding: 16, minHeight: 160, fontFamily: fonts.bodyMed, fontSize: 15, color: colors.ink, textAlignVertical: 'top' },
  softInput: { backgroundColor: colors.lavender, borderRadius: radius.lg, paddingVertical: 12, paddingHorizontal: 12, fontFamily: fonts.bodyMed, fontSize: 15, color: colors.ink },
  softSelectText: { fontFamily: fonts.bodyMed, fontSize: 15, color: colors.ink },
  charCount: { fontFamily: fonts.bodyBold, fontSize: 10, color: colors.muted, textAlign: 'right' },
  formRow: { flexDirection: 'row', gap: 16, flexWrap: 'wrap' },
  formActions: { flexDirection: 'row', alignItems: 'center', gap: 16, marginTop: 16 },
  findBtn: { backgroundColor: '#F97316', borderWidth: 2, borderColor: colors.slate900 },
  findBtnText: { color: colors.slate900 },
  note: { color: colors.orangeDeep, fontFamily: fonts.bodyBold, fontSize: 13 },

  back: { flexDirection: 'row', alignItems: 'center' },
  backText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.teal },

  deepenBanner: { borderRadius: radius.lg, paddingHorizontal: 28, paddingVertical: 20, flexDirection: 'row', alignItems: 'center', gap: 20, flexWrap: 'wrap' },
  deepenTitle: { fontFamily: fonts.bodyBold, fontSize: 16, color: colors.white },
  deepenSub: { fontFamily: fonts.bodyMed, fontSize: 14, color: colors.grayLighter, marginTop: 2 },
  deepenRight: { alignItems: 'center', gap: 8 },
  deepenBtn: { backgroundColor: colors.orangeDeep, borderRadius: radius.pill, paddingHorizontal: 20, paddingVertical: 10 },
  deepenBtnText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.white },
  deepenAlt: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.grayLighter, textDecorationLine: 'underline' },

  filterBar: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap', zIndex: 20 },
  filterLabel: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.muted, letterSpacing: 0.6 },
  filterToggle: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 8 },
  filterToggleHovered: { transform: [{ translateX: -1 }, { translateY: -1 }] },
  filterTogglePressed: { transform: [{ translateX: 2 }, { translateY: 2 }] },
  filterToggleOn: { backgroundColor: colors.lavender },
  filterToggleText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate900 },
  // Invisible press-catcher for "click outside to close". Sits BELOW the filter bar
  // (zIndex 20) and above the page content, deliberately: at a higher zIndex it would also
  // swallow presses on the facet toggles, so switching from one facet to another would take
  // two clicks (close, then open) instead of one. The panel itself is inside the bar's
  // stacking context, so it stays above this too.
  filterBackdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, zIndex: 10 },
  clearFilters: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.md },
  clearFiltersText: { fontFamily: fonts.body, fontSize: 13, fontWeight: '700', color: colors.orangeDeep, textDecorationLine: 'underline' },
  facetPanel: { position: 'absolute', top: '100%', marginTop: 8, width: 224, backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.lg, padding: 12, zIndex: 50, gap: 2 },
  facetPanelWide: { width: 320 },
  facetPanelLeft: { left: 0 },
  facetPanelRight: { right: 0 },
  facetScroll: { maxHeight: 320 },
  facetRow: { paddingVertical: 4 },
  facetRowText: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 16, color: colors.slate900 },

  resultCard: { backgroundColor: colors.white, borderWidth: 4, borderColor: colors.slate900, borderRadius: radius.xxl, padding: 24, gap: 16 },
  // Raised only while a popover is open, so the panel overlaps the cards BELOW this one
  // (equal z-index means the later sibling wins). Deliberately below filterBar's 20: the
  // cards come after the filter bar in source order, so at 20 they would tie and a raised
  // card would cover an open filter panel.
  resultCardReviewOpen: { zIndex: 15 },
  resultCardHovered: { transform: [{ translateX: -2 }, { translateY: -2 }] },
  resultCardSelected: { borderColor: '#A3E635', backgroundColor: '#F7FEE7' },
  // zIndex on both rows is what lets ReviewBadge's popover paint OVER the card's later
  // content instead of under it. RN-web gives every View `position: relative; z-index: 0`,
  // which creates a stacking context per row and traps the popover inside this one — see the
  // STACKING note on ReviewBadge in src/ui/components.tsx. 1 is enough (it only has to beat
  // the card's other children at 0) and stays clear of the filter bar's 20.
  cardTopRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', zIndex: 1 },
  badgeRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap', flexShrink: 1, zIndex: 1 },
  trackedTag: { backgroundColor: '#1E293B', borderRadius: radius.pill, paddingHorizontal: 16, paddingVertical: 8 },
  trackedTagText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.white },
  saveBtn: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.pill, paddingHorizontal: 20, paddingVertical: 10 },
  saveBtnHovered: { transform: [{ translateX: -1 }, { translateY: -1 }] },
  saveBtnPressed: { transform: [{ translateX: 2 }, { translateY: 2 }] },
  saveBtnSelected: { backgroundColor: '#A3E635' },
  saveBtnText: { fontFamily: fonts.bodyXBold, fontSize: 12, color: colors.slate900 },
  resultName: { fontFamily: fonts.display, fontSize: 30, lineHeight: 36, color: colors.slate900 },
  whyRow: { flexDirection: 'row', gap: 12 },
  // reason (keyword/rank fit) gets the yellow bar; AI profile-tag reasoning gets indigo
  // (resultCardHTML: bg-yellow-400 vs bg-indigo-400).
  whyBar: { width: 4, borderRadius: 2, backgroundColor: '#FACC15' },
  whyBarIndigo: { backgroundColor: '#818CF8' },
  whyLabel: { fontFamily: fonts.bodyBold, fontSize: 10, color: colors.slate400, letterSpacing: 0.8, marginBottom: 4 },
  whyText: { fontFamily: fonts.display, fontSize: 20, lineHeight: 26, color: colors.slate900 },
  metaRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  metaPill: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.indigo200, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 6 },
  metaPillText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate900 },
  summary: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.slate500 },

  resultsWrap: { flex: 1 },
  selectionBar: {
    position: 'absolute',
    bottom: 16,
    left: 16,
    right: 16,
    maxWidth: 832,
    marginHorizontal: 'auto',
    backgroundColor: colors.slate900,
    borderRadius: radius.lg,
    padding: 16,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
  },
  selectionCount: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.white },
});
