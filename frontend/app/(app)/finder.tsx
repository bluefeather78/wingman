import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { addTrackerItemChecked, flattenItems, loadTrackerData } from '@/api/trackerStore';
import type { MatchRequest, Opportunity } from '@/api/types';
import { PROFILE_SUFFICIENT_LENGTH } from '@/lib/constants';
import { ACTIVE_KINDS, KIND_CONFIG } from '@/lib/kinds';
import { countProfileWords } from '@/lib/profile';
import { type EnrichedTag } from '@/lib/profileTags';
import {
  cachedProfileFilterTags,
  getProfileDerived,
  getProfileFilterValues,
  refreshProfileDerived,
  type FilterTagsSlot,
  type ModelCalls,
  type ProfileRecord,
  type ProfileStore,
} from '@/lib/profileDerived';
import { parseGradeFromText } from '@/lib/grade';
import { extractJSON } from '@/lib/extractJSON';
import { inferSubjects, preFilter, rankCandidates, type RankedPick } from '@/lib/ranking';
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
import { colors, fonts, popShadow, radius, space } from '@/ui/theme';

interface Result {
  opp: Opportunity;
  reason: string;
  tier: 'strong' | 'look';
  // Which kind's ranking call surfaced this. Set by the profile-driven fan-out; absent on a
  // single-kind form search. Preferred over deriving a kind from opp.type, which only ever
  // guessed at what the search actually did.
  kind?: string;
  // Carried through from POST /api/match (suggest path only): the raw cosine score and the
  // server's "strong match" flag. `tier` already encodes strong for the badge, but these
  // ride along so the card can show the score if it ever wants to.
  score?: number | null;
  strong?: boolean;
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
type Stage = 'home' | 'quiz' | 'form' | 'results';

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

// Quiz: root → sub-branch → kind (from script.js QUIZ_BRANCHES + the live quiz screen).
const QUIZ_ROOT = [
  { label: 'I already have a research paper or project', desc: 'In progress or already completed', branch: 'project' },
  { label: "I'm looking for something to do when school is out", desc: 'Camps, programs, or work experience', branch: 'timeoff' },
  { label: 'I enjoy competing directly with my peers', desc: 'Tests, exams, head-to-head challenges', kind: 'pure-competition' },
] as const;
const QUIZ_SUB: Record<string, { label: string; desc: string; kind: string }[]> = {
  project: [
    { label: 'Enter it in a competition', desc: 'Science fairs, app challenges, project contests', kind: 'research-competition' },
    { label: 'Present it at a conference', desc: 'Submit a paper to a workshop or conference', kind: 'conference' },
    { label: 'Get it published', desc: 'Submit to an academic or student journal', kind: 'journal' },
  ],
  timeoff: [
    { label: 'Hands-on work experience', desc: 'Work with a lab, company, or organization', kind: 'internship' },
    { label: 'A summer program', desc: 'Camps, pre-college programs, academies', kind: 'summer' },
    { label: 'Volunteering or service', desc: 'Give time to a cause or community organization', kind: 'volunteer' },
  ],
};

// How many of the recall pool's best rows get a "why it fits" reason. Matches rankCandidates'
// own 10-12 cap; these are the rows above the fold that the student actually reads.
const REASON_TOP_N = 12;

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
  // Seed from the last-loaded profile synchronously so the theme picker paints on the FIRST
  // render, not after an /api/data/load round trip (expo-router remounts this screen on every
  // visit). peekData never replaces the fetch below — it just skips the wait when we already
  // have the data. Undefined on a cold cache; the async load then fills it in.
  const [profileText, setProfileText] = useState(
    () => httpClient.peekData<ProfileRecord>('student-profile')?.synthesized ?? '',
  );
  // "Your profile is empty" is also what an unloaded profile looks like, so the hero flashed
  // that on every visit before the fetch landed. Gate it on the load actually resolving.
  const [profileLoaded, setProfileLoaded] = useState(false);
  // Come back to the tab and you land back ON the list, not on a hero telling you it
  // exists. The results survived the unmount (sessionSearch); making the student press
  // "View my matches" to see them again is a step that only exists because of how this
  // screen is built.
  const [stage, setStage] = useState<Stage>(() => (sessionSearch?.results.length ? 'results' : 'home'));
  const [browseOpen, setBrowseOpen] = useState(false);
  const [quizBranch, setQuizBranch] = useState<string | null>(null);
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
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [trackedIds, setTrackedIds] = useState<Set<string>>(new Set());
  // Hover-lift for result cards (.pop-card:hover in the live app) — one shared id rather
  // than a hook per card, since the card list is rendered via .map(), not its own component.
  const [hoveredCardId, setHoveredCardId] = useState<string | null>(null);
  // Which card's review popover is open. One id for the whole list, so opening a second one
  // closes the first — the rule toggleReviewInfo() enforced in the retired SPA.
  const [openReviewId, setOpenReviewId] = useState<string | null>(null);
  const [hoveredSaveBtnId, setHoveredSaveBtnId] = useState<string | null>(null);
  const [pressedSaveBtnId, setPressedSaveBtnId] = useState<string | null>(null);
  const [hoveredQuizOption, setHoveredQuizOption] = useState<number | null>(null);
  const [pressedQuizOption, setPressedQuizOption] = useState<number | null>(null);
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
  // Seed the theme chips from the cached profile synchronously (see profileText above) — this
  // is what makes the picker grid appear instantly instead of after the profile round trip.
  const [profileTags, setProfileTags] = useState<EnrichedTag[]>(() => {
    const p = httpClient.peekData<ProfileRecord>('student-profile');
    const cached = p ? cachedProfileFilterTags(p) : null;
    return (cached || []).filter((t) => t && typeof t.tag === 'string');
  });
  // Which themes drive the recall query (PR4). This is now the profile facet: a MULTI-select
  // of themes that, on change, re-POSTs /api/match for a fresh recall (see rerunThemeMatch),
  // rather than the old single-tag client re-score. Initialized to all-selected once the
  // tags load (below). An empty set is treated as "all themes" so deselecting everything can
  // never send an empty query.
  const [selectedThemes, setSelectedThemes] = useState<Set<string>>(new Set());
  // Mirror of selectedThemes for the debounced re-run to read the latest picks at fire time.
  const selectedThemesRef = useRef(selectedThemes);
  useEffect(() => { selectedThemesRef.current = selectedThemes; }, [selectedThemes]);
  // "Explore something new" — a free-text direction that is NOT one of the profile's themes.
  // It is added to the recall query as its own theme, so a student can search a brand-new
  // interest (or a stretch) that their profile doesn't mention yet. Mirrored to a ref for the
  // same reason selectedThemes is — the debounced facet re-run reads the latest at fire time.
  const [exploreText, setExploreText] = useState('');
  const exploreTextRef = useRef(exploreText);
  useEffect(() => { exploreTextRef.current = exploreText; }, [exploreText]);
  // The single theme (or explore direction) the currently-shown results were searched for,
  // captured at search time so the "Showing matches for" header can't drift from the results.
  const [resultsTheme, setResultsTheme] = useState<string | null>(null);
  // Debounce rapid facet toggles into one recall call.
  const themeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // A recall (POST /api/match) is in flight from the theme facet — the grid shows a loading
  // state and holds the old cards back until the fresh pool lands.
  const [themeMatching, setThemeMatching] = useState(false);
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [tagScores, setTagScores] = useState<Record<string, TagScore> | null>(null);
  const [tagScoring, setTagScoring] = useState(false);
  const tagScoreCache = useRef(sessionSearch?.tagScores ?? new Map<string, Record<string, TagScore>>());
  // True while the tag slot is being generated for a profile that has none yet.
  const [tagsBuilding, setTagsBuilding] = useState(false);

  // Themes start UNSELECTED — the student deliberately picks where to start (and must pick at
  // least one, or type an "explore" direction, before we search). No auto-select-all.

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
      if (themeTimerRef.current) clearTimeout(themeTimerRef.current);
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

  // PR4: the profile-based suggestion no longer fires automatically. A ready profile now
  // lands on the theme picker (the home hero's ready branch) so the student chooses which
  // themes to start with before we recall — the "soft start" the merge plan calls for. This
  // effect only settles the boot state so the hero stops showing the loading row.
  const autoRan = useRef(false);
  // Whether the boot decision has been MADE yet. Until both the catalog and the profile have
  // landed we cannot know which hero to draw, and rendering one in that gap makes it blink a
  // frame later. A ref can't drive this: the hero has to re-render when it settles.
  const [autoRunSettled, setAutoRunSettled] = useState(!!sessionSearch?.results.length);
  useEffect(() => {
    if (autoRan.current) return;
    if (!opps || !profileLoaded) return; // still deciding — keep showing the loading state
    autoRan.current = true;
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

  // The themes to send as the recall query: exactly the picked profile themes, PLUS the
  // free-text "explore something new" direction (as its own theme) when the student typed one.
  // No fall-back-to-all — the button enforces at least one, so an empty query never runs.
  function themeTagsFor(sel: Set<string>): EnrichedTag[] {
    const picked = profileTags.filter((t) => sel.has(t.tag));
    const extra = exploreTextRef.current.trim();
    return extra ? [...picked, { tag: extra }] : picked;
  }

  // Grade the same way the form path resolves it: the form dropdown wins, else the grade the
  // profile's stored filter values inferred. getProfileFilterValues is cached per profile
  // text, so this is not a per-search paid call.
  async function resolveGradeNum(): Promise<number | null> {
    let profileGrade: number | null = null;
    try {
      const fv = await getProfileFilterValues(profileStore, modelCalls, profileRecord.current);
      profileGrade = fv.grade;
    } catch {
      /* best effort — no grade just means no grade filter */
    }
    return parseGradeFromText(grade) ?? profileGrade;
  }

  function buildMatchBlob(themeTags: EnrichedTag[], gradeNum: number | null): MatchRequest {
    const state = homeState.trim();
    return {
      grade: gradeNum,
      ...(state ? { location: { state } } : {}),
      profile_themes: themeTags.map((t) => ({
        theme: t.tag,
        intent: t.intent ?? null,
        next_steps: (t.nextSteps || []).join('; ') || null,
      })),
      // Not available on this branch — the recall runs on themes alone here.
      highlight_projects: [],
    };
  }

  // POST /api/match and map its flattened rows into the finder's Result grid. Each result IS
  // an Opportunity row plus score/strong; tier is derived from strong so the existing "Strong
  // Fit" badge renders with no card changes.
  async function callMatchMapped(
    themeTags: EnrichedTag[],
    gradeNum: number | null,
  ): Promise<{ mapped: Result[]; note: string | null }> {
    // Single-select: themeTags holds exactly one entry (the chosen theme OR the explore text),
    // so its tag is what these results are "for". Captured here so the header is always accurate.
    setResultsTheme(themeTags[0]?.tag ?? null);
    const resp = await httpClient.match(buildMatchBlob(themeTags, gradeNum));
    const rows = resp.results || [];
    // "WHY IT FITS" — reintroduced from main's ranker (rankCandidates), NOT the retired
    // curation pass. Semantic recall (embeddings) does the coarse cut server-side; here one
    // Gemini call writes a specific second-person reason for the best rows and judges each
    // 'strong'/'look'. That LLM tier — not the profile-dependent cosine magnitude — drives the
    // Strong Fit badge, so a great entrepreneurship match reads "strong" even though its
    // absolute cosine (~0.57) is lower than a robotics match's (~0.73). Only the top slice is
    // reasoned (the model caps at 10-12 and those are the rows the student actually reads);
    // the rest stay "worth a look". A reasoning failure degrades to no reason, never a crash.
    // Describe the student to the reasoner from the CHOSEN themes' theme+intent+nextSteps —
    // not raw profileText. Measured on real profiles: this makes the reason concrete and
    // goal-aligned ("take Adio from concept to market") and gives the GOAL-FORMAT rule real
    // signal, where the whole-profile text was unfocused on what the student actually searched.
    const themeDesc = themeTags
      .map((t) => [t.tag, t.intent || '', (t.nextSteps || []).join('; ')].filter(Boolean).join('. '))
      .filter(Boolean)
      .join('\n');
    const reasonDesc = themeDesc || profileText;
    const reasons: Record<string, { reason: string; tier: 'strong' | 'look' }> = {};
    const top = rows.slice(0, REASON_TOP_N) as unknown as Opportunity[];
    if (top.length) {
      // One reasoning call produces every card's "why it fits", so a single failure wipes them
      // ALL — retry once after a short backoff before degrading to no reasons. callGeminiJSON
      // already retries a parse failure internally; this covers a transient network/API error.
      let ranked: RankedPick[] = [];
      try {
        ranked = await rankCandidates(callGemini, reasonDesc, top, buildPrefs() || null, false);
      } catch (e1) {
        console.warn('why-it-fits reasoning failed once, retrying:', (e1 as Error).message);
        try {
          await new Promise((r) => setTimeout(r, 1200));
          ranked = await rankCandidates(callGemini, reasonDesc, top, buildPrefs() || null, false);
        } catch (e2) {
          console.warn('why-it-fits reasoning failed after retry, showing matches without reasons:', (e2 as Error).message);
        }
      }
      ranked.forEach((p) => {
        if (p && p.id) reasons[p.id] = { reason: p.reason || '', tier: p.tier === 'strong' ? 'strong' : 'look' };
      });
    }
    const mapped: Result[] = rows.map((row) => {
      const rz = reasons[row.id];
      return {
        opp: row,
        reason: rz?.reason ?? '',
        tier: rz ? rz.tier : 'look',
        score: row.score ?? null,
        strong: rz ? rz.tier === 'strong' : false,
      };
    });
    return { mapped, note: resp.note ?? null };
  }

  // The theme facet's fresh recall. Runs the debounced re-POST against the current picks,
  // shows a grid loading state, and replaces the pool. A failure keeps the existing list and
  // surfaces the reason rather than blanking the grid.
  async function rerunThemeMatch() {
    if (!profileReady) return;
    setThemeMatching(true);
    try {
      const gradeNum = await resolveGradeNum();
      const { mapped, note: matchNote } = await callMatchMapped(themeTagsFor(selectedThemesRef.current), gradeNum);
      if (!aliveRef.current) return;
      setResults(mapped);
      setNote(matchNote);
      rememberSearch(mapped, matchNote, null);
      setSelected(new Set());
      setVisibleCount(10);
    } catch (e) {
      if (aliveRef.current) setNote(`Couldn't refresh matches: ${(e as Error).message}`);
    } finally {
      if (aliveRef.current) setThemeMatching(false);
    }
  }

  function scheduleThemeRerun() {
    if (themeTimerRef.current) clearTimeout(themeTimerRef.current);
    themeTimerRef.current = setTimeout(() => { void rerunThemeMatch(); }, 500);
  }
  // SINGLE-SELECT: exactly one theme at a time. Picking a theme replaces the selection
  // (picking the current one again clears it) and clears any "explore" text — a chosen theme
  // and a typed direction are mutually exclusive.
  function pickTheme(tag: string) {
    setSelectedThemes((prev) => (prev.has(tag) ? new Set<string>() : new Set([tag])));
    setExploreText('');
  }
  // Typing a direction clears the selected theme (the other half of the mutual exclusion).
  function onExploreChange(text: string) {
    setExploreText(text);
    if (text.trim()) setSelectedThemes(new Set());
  }
  // Results-view Themes dropdown: pick one theme AND re-run recall (debounced). The initial
  // picker uses pickTheme directly (no re-run — the "Find my matches" button triggers it).
  function toggleTheme(tag: string) {
    pickTheme(tag);
    scheduleThemeRerun();
  }
  function onFacetExploreChange(text: string) {
    onExploreChange(text);
    scheduleThemeRerun();
  }

  async function search(desc: string, k: string | null, prefs: string) {
    if (!opps || !desc.trim() || searching) return;
    setSearching(true);
    setNote(null);
    const cfg = k ? KIND_CONFIG[k] : null;
    const strict = !!cfg?.strictType;
    try {
      // Subjects + grade come from the profile's stored filter values, recomputed only when
      // the profile itself meaningfully changes — this was an unconditional Gemini call on
      // every search, which both cost money per search and let the same profile produce
      // different subjects (and so different results) each time.
      let subjectHints: string[] = [];
      let profileGrade: number | null = null;
      try {
        const fv = await getProfileFilterValues(profileStore, modelCalls, profileRecord.current);
        subjectHints = fv.subjects;
        profileGrade = fv.grade;
      } catch {
        /* best effort — no hints just means keyword-only scoring */
      }
      // The form's dropdown wins when set; otherwise the grade comes from whatever
      // grade-level language the student's own profile text happens to contain, if any.
      const gradeNum = parseGradeFromText(grade) ?? profileGrade;

      // ---- The profile-driven path is now semantic recall (PR4) ----
      // Instead of the per-kind preFilter + rankCandidates fan-out, the suggest path posts the
      // student's selected themes to /api/match: the server embeds them, recalls the top rows
      // by cosine, drops verified-ineligible ones, and returns the whole scored pool. The grid,
      // pool facets and add-to-tracker are unchanged — only how the pool is produced. The
      // non-suggest (form/quiz) path below keeps preFilter/rankCandidates. subjectHints is now
      // unused here (embeddings supersede it) but still feeds the form path.
      if (!k) {
        const { mapped, note: matchNote } = await callMatchMapped(themeTagsFor(selectedThemes), gradeNum);
        setResults(mapped);
        setNote(matchNote);
        rememberSearch(mapped, matchNote, k);
        setSelected(new Set());
        setVisibleCount(10);
        setStage('results');
        return;
      }

      const { pool, typeMatches, widened, strictEmpty } = preFilter(
        opps, desc, subjectHints, cfg?.dbTypes ?? null, strict, gradeNum,
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
            /* PR4 theme picker: the student deliberately chooses where to start. Themes are
               UNSELECTED by default; at least one theme (or an "explore something new"
               direction) is required before we recall. */
            <>
              <Text style={styles.heroTitle}>What would you like to start with?</Text>
              <Text style={[styles.heroSub, styles.heroSubItalic]}>
                Pick one — you can always change it later in the filters.
              </Text>
              {tagsBuilding && !profileTags.length ? (
                <LoadingRow title="Building your themes…" inline />
              ) : profileTags.length ? (
                <View style={styles.themeChips}>
                  {profileTags.map((t) => {
                    const on = selectedThemes.has(t.tag);
                    return (
                      <Pressable
                        key={t.tag}
                        style={[styles.themeChip, on && styles.themeChipOn]}
                        onPress={() => pickTheme(t.tag)}
                      >
                        <Text style={[styles.themeChipText, on && styles.themeChipTextOn]}>
                          {on ? '✓ ' : ''}{t.tag}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
              {/* Explore something new: a free-text direction outside the profile's themes.
                  Mutually exclusive with a chosen theme (single-select). */}
              <Text style={styles.exploreLabel}>Or explore something new</Text>
              <SoftInput
                value={exploreText}
                onChangeText={onExploreChange}
                placeholder="e.g. marine biology, game design, journalism…"
              />
              <PopButton
                label="Find my matches →"
                onPress={() => void suggestForMe()}
                disabled={!selectedThemes.size && !exploreText.trim()}
                style={styles.selfStart}
              />
            </>
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
            <Pressable style={styles.quizCta} onPress={() => { setQuizBranch(null); setStage('quiz'); }}>
              <Text style={styles.quizCtaText}>Not sure? Take a quick quiz →</Text>
            </Pressable>
          </SoftCard>
        )}
      </Screen>
    );
  }

  // ---------- Quiz stage ----------
  if (stage === 'quiz') {
    const options = quizBranch ? QUIZ_SUB[quizBranch] : null;
    return (
      <Screen>
        <BackLink label="Back to opportunity type" onPress={() => (quizBranch ? setQuizBranch(null) : setStage('home'))} />
        <SoftCard style={{ gap: 24, padding: 32 }}>
          <Txt variant="h2">Let's figure out what fits</Txt>
          <Text style={styles.quizQuestion}>{quizBranch ? 'And with that, what do you want to do?' : 'Which of these sounds most like you right now?'}</Text>
          <View style={{ gap: 12 }}>
            {(options ?? QUIZ_ROOT).map((o, i) => (
              <Pressable
                key={i}
                onHoverIn={() => setHoveredQuizOption(i)}
                onHoverOut={() => setHoveredQuizOption((cur) => (cur === i ? null : cur))}
                onPressIn={() => setPressedQuizOption(i)}
                onPressOut={() => setPressedQuizOption((cur) => (cur === i ? null : cur))}
                style={[
                  styles.quizOption,
                  i === 0 && { backgroundColor: '#EDF7FC' },
                  popShadow(pressedQuizOption === i ? 1 : hoveredQuizOption === i ? 4 : 3),
                  pressedQuizOption === i
                    ? styles.quizOptionPressed
                    : hoveredQuizOption === i && styles.quizOptionHovered,
                ]}
                onPress={() => {
                  const opt = o as { kind?: string; branch?: string };
                  if (opt.kind) openForm(opt.kind);
                  else if (opt.branch) setQuizBranch(opt.branch);
                }}
              >
                <Text style={styles.quizOptTitle}>{o.label}</Text>
                <Text style={styles.quizOptDesc}>{o.desc}</Text>
              </Pressable>
            ))}
          </View>
        </SoftCard>
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

      {/* Which theme these results are for — captured at search time so it can't drift. */}
      {resultsTheme && results.length > 0 && (
        <View style={styles.resultsForRow}>
          <Text style={styles.resultsForLabel}>SHOWING MATCHES FOR</Text>
          <View style={styles.resultsForPill}>
            <Text style={styles.resultsForPillText}>{resultsTheme}</Text>
          </View>
        </View>
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
              <Text style={styles.filterToggleText}>
                ▾ Themes
              </Text>
            </Pressable>
            {openFacet === 'profile' && (
              /* The ONE facet that re-searches. Picking a theme re-POSTs /api/match for a fresh
                 recall (debounced), unlike the pool facets below, which narrow the returned rows
                 client-side for free. SINGLE-SELECT (one theme at a time), so it reads as a radio
                 list; the "Or explore something new" box mirrors the initial picker and is
                 mutually exclusive with the theme rows. Scrolls, because the theme list is as
                 long as the profile is broad. */
              <View style={[styles.facetPanel, styles.facetPanelWide, facetAlign.profile === 'right' ? styles.facetPanelRight : styles.facetPanelLeft]}>
                <Text style={styles.facetHint}>Changing this finds new matches.</Text>
                <ScrollView style={styles.facetScroll} nestedScrollEnabled>
                  {profileTags.map((t) => {
                    const on = selectedThemes.has(t.tag);
                    return (
                      <Pressable key={t.tag} style={styles.facetRow} onPress={() => toggleTheme(t.tag)}>
                        <Text style={styles.facetRowText}>{on ? '●' : '○'} {t.tag}</Text>
                      </Pressable>
                    );
                  })}
                </ScrollView>
                <Text style={styles.facetExploreLabel}>Or explore something new</Text>
                <SoftInput
                  value={exploreText}
                  onChangeText={onFacetExploreChange}
                  placeholder="Type a new direction…"
                />
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
      {/* A theme-facet recall is in flight — hold the grid and its empty states back so a
          brief in-between frame never reads as "no matches". */}
      {themeMatching && <LoadingRow title="Finding new matches…" sub="Re-searching for your updated themes." inline />}
      {tagScoring && <LoadingRow title="Scoring matches against your profile…" inline />}
      {tagsBuilding && <LoadingRow title="Building your profile filters…" inline />}

      {/* Empty states. These are two genuinely different situations and must not share a
          message: "the search found nothing" is about the search, "your filters hid
          everything" is about a control the student can undo right here. Before this the
          page simply rendered nothing at all, which reads as broken rather than empty. */}
      {!themeMatching && !results.length && (
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
      {!themeMatching && !!results.length && !filteredResults.length && (
        <SoftCard style={styles.emptyCard}>
          <Text style={styles.heroTitle}>Your filters hid everything</Text>
          <Text style={[styles.heroSub, styles.heroSubItalic]}>
            {results.length} match{results.length === 1 ? '' : 'es'} {results.length === 1 ? 'is' : 'are'} waiting behind the filters above.
          </Text>
          <PopButton label="Clear all filters" onPress={clearAllFilters} style={styles.selfStart} />
        </SoftCard>
      )}

      {/* Result cards */}
      {!themeMatching && visibleResults.map(({ opp, reason, tier, kind: resultKind, aiReasoning, aiRank }) => {
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

      {!themeMatching && filteredResults.length > visibleCount && (
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
  // Marks the profile/theme facet as the one that re-searches, vs the pool facets that narrow.
  facetHint: { fontFamily: fonts.bodyBold, fontSize: 10, color: colors.muted, letterSpacing: 0.4, marginBottom: 6 },

  // First-screen theme picker chips (PR4). Selected = filled lavender; unselected = outlined.
  exploreLabel: { fontFamily: fonts.bodyBold, fontSize: 13, color: colors.inkSoft, marginTop: 14, marginBottom: 6 },
  facetExploreLabel: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.inkSoft, marginTop: 10, marginBottom: 6 },
  resultsForRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 12 },
  resultsForLabel: { fontFamily: fonts.bodyBold, fontSize: 11, letterSpacing: 0.5, color: colors.muted },
  resultsForPill: { backgroundColor: colors.lavender, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 5 },
  resultsForPillText: { fontFamily: fonts.bodyBold, fontSize: 13, color: colors.slate900 },
  themeChips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 8, marginBottom: 4 },
  themeChip: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate400, borderRadius: radius.pill, paddingHorizontal: 14, paddingVertical: 8 },
  themeChipOn: { backgroundColor: colors.lavender, borderColor: colors.slate900 },
  themeChipText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500 },
  themeChipTextOn: { color: colors.slate900 },

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
