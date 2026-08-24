import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import { addTrackerItem, flattenItems, loadTrackerData } from '@/api/trackerStore';
import type { Opportunity } from '@/api/types';
import { PROFILE_SUFFICIENT_LENGTH } from '@/lib/constants';
import { ACTIVE_KINDS, KIND_CONFIG } from '@/lib/kinds';
import { countProfileWords } from '@/lib/profile';
import { parseGradeFromText } from '@/lib/grade';
import { extractJSON } from '@/lib/extractJSON';
import { inferSubjects, preFilter, rankCandidates, type RankedPick } from '@/lib/ranking';
import { markNewlyAdded } from '@/lib/newlyAdded';
import { applyDeadlineCheckToInfo, extractTrackerInfo, findBucketForKind } from '@/lib/tracker';
import { MiniBadge, PopButton, Screen, SoftCard, Txt, usePopInteraction } from '@/ui/components';
import { colors, fonts, popShadow, radius, space } from '@/ui/theme';

interface Result {
  opp: Opportunity;
  reason: string;
  tier: 'strong' | 'look';
}
const callGemini = httpClient.callGemini.bind(httpClient);
type Stage = 'home' | 'quiz' | 'form' | 'results';

// Map a catalog opportunity's `type` to a kind key (used when adding from a mixed suggest list).
function kindForOpp(opp: Opportunity): string {
  const map: Record<string, string> = {
    Program: 'summer', Internship: 'internship', Conference: 'conference',
    Journal: 'journal', Research: 'research-competition', Competition: 'pure-competition',
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
  ],
};

const FILTER_FIELDS = [
  { key: 'type', label: 'Type' },
  { key: 'price', label: 'Cost' },
  { key: 'season', label: 'Season' },
  { key: 'location', label: 'Format' },
] as const;
type FilterKey = (typeof FILTER_FIELDS)[number]['key'];

// The "Your Profile" facet's enriched tags, cached on the shared student-profile record
// by the old app (PROFILE_DERIVED_SLOTS.filterTags) — read for free, never regenerated here.
interface EnrichedTag {
  tag: string;
  intent?: string;
  nextSteps?: string[];
}
interface TagScore {
  reasoning?: string;
  rank: number;
}

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
  const [profileText, setProfileText] = useState('');
  // "Your profile is empty" is also what an unloaded profile looks like, so the hero flashed
  // that on every visit before the fetch landed. Gate it on the load actually resolving.
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [stage, setStage] = useState<Stage>('home');
  const [browseOpen, setBrowseOpen] = useState(false);
  const [quizBranch, setQuizBranch] = useState<string | null>(null);
  const [kind, setKind] = useState<string>(ACTIVE_KINDS[0]);
  const [suggestMode, setSuggestMode] = useState(false);

  const [description, setDescription] = useState('');
  const [grade, setGrade] = useState('');
  const [homeState, setHomeState] = useState('');
  const [freeOnly, setFreeOnly] = useState(false);
  const [remote, setRemote] = useState(false);

  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<Result[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [trackedIds, setTrackedIds] = useState<Set<string>>(new Set());
  // Hover-lift for result cards (.pop-card:hover in the live app) — one shared id rather
  // than a hook per card, since the card list is rendered via .map(), not its own component.
  const [hoveredCardId, setHoveredCardId] = useState<string | null>(null);
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
  const [profileTags, setProfileTags] = useState<EnrichedTag[]>([]);
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [tagScores, setTagScores] = useState<Record<string, TagScore> | null>(null);
  const [tagScoring, setTagScoring] = useState(false);
  const tagScoreCache = useRef(new Map<string, Record<string, TagScore>>());

  useEffect(() => {
    let alive = true;
    // Discontinued programs never reach the results, the browse list, or the ranker —
    // filtered at the source so no path can miss it. The test is `=== 'not_running'`, never
    // `!== 'running'`: the catalog's `status` is NULL on 1195 of its 1239 active rows (never
    // deadline-checked), and reading that absence as "not running" would empty Fresh Finds.
    httpClient
      .getOpportunities()
      .then((r) => alive && setOpps(r.filter((o) => o.status !== 'not_running')))
      .catch((e) => alive && setOppsError((e as Error).message));
    httpClient
      .loadData<{ synthesized?: string; filterTags?: { enrichedTags?: EnrichedTag[] } }>('student-profile')
      .then((p) => {
        if (!alive) return;
        setProfileText(p?.synthesized ?? '');
        const tags = p?.filterTags?.enrichedTags;
        if (Array.isArray(tags)) setProfileTags(tags.filter((t) => t && typeof t.tag === 'string'));
      })
      .catch(() => {})
      .finally(() => {
        if (alive) setProfileLoaded(true);
      });
    loadTrackerData().then((d) => alive && setTrackedIds(new Set(flattenItems(d).map((i) => i.id)))).catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

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
  useEffect(() => {
    if (autoRan.current) return;
    if (opps && profileReady && stage === 'home' && !results.length) {
      autoRan.current = true;
      void suggestForMe();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opps, profileReady]);

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
    const gradeNum = parseGradeFromText(grade);
    try {
      let subjectHints: string[] = [];
      try {
        subjectHints = await inferSubjects(callGemini, desc);
      } catch {
        /* best effort */
      }
      const pool = preFilter(opps, desc, subjectHints, cfg?.dbTypes ?? null, strict, gradeNum);
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
      } catch (err) {
        console.error('AI ranking unavailable, falling back to keyword order:', (err as Error).message);
        setNote('Showing keyword matches — AI ranking is unavailable right now.');
        setResults(pool.slice(0, 12).map((opp) => ({ opp, reason: '', tier: 'look' as const })));
      }
      setSelected(new Set());
      setVisibleCount(10);
      setStage('results');
    } catch (e) {
      setNote(`Search failed: ${(e as Error).message}`);
    } finally {
      setSearching(false);
    }
  }

  async function suggestForMe() {
    setSuggestMode(true);
    await search(profileText, null, buildPrefs());
  }

  // Ported from script.js's bulk-add flow: a full extractTrackerInfo() web-search pass per
  // opportunity (retried once on failure), then the shared/cached on-demand deadline check
  // overlaid on top — the same two-step sequence buildTracker() used, dropped somewhere in
  // the RN port in favor of a bare getDeadlineCheck() call that left status/note/requirements
  // /action items empty. A total failure falls back to a database-only stub, exactly as before.
  async function addOneToTracker(opp: Opportunity, reason: string) {
    const bucket = findBucketForKind(suggestMode ? kindForOpp(opp) : kind);
    const url = (opp.url as string) ?? null;
    const type = (opp.type as string) ?? null;
    const reviewStatus = (opp.review_status as string) ?? null;
    const reviewSummary = (opp.review_summary as string) ?? null;
    const summary = (opp.summary as string) || '';
    try {
      let info;
      try {
        info = await extractTrackerInfo(callGemini, opp);
      } catch (firstErr) {
        console.warn(`Retrying ${opp.name} after error:`, (firstErr as Error).message);
        info = await extractTrackerInfo(callGemini, opp);
      }
      applyDeadlineCheckToInfo(info, await httpClient.getDeadlineCheck(opp.id));
      await addTrackerItem(bucket, {
        id: opp.id,
        name: opp.name,
        url,
        type,
        bucket,
        progressStatus: 'not_started',
        status: ['running', 'not_running', 'unknown'].includes(info.status) ? info.status : 'unknown',
        reviewStatus,
        reviewSummary,
        meta: info.meta || [opp.org, opp.type, opp.price, opp.location].filter(Boolean).join(' · '),
        fit: info.fit || reason || summary,
        note: info.note || 'Details from the opportunities database — confirm on the official site.',
        noteType: info.status === 'not_running' ? 'flag' : (info.noteType || 'plain'),
        importantDates: Array.isArray(info.important_dates)
          ? info.important_dates
              .filter((d) => d && d.date_iso)
              .map((d) => ({ label: d.label || 'Date', dateISO: d.date_iso, type: d.type || 'deadline' }))
              .sort((a, b) => a.dateISO.localeCompare(b.dateISO))
          : [],
        deadlineLabel: info.deadline_label || 'CHECK SITE',
        wasEstimated: !!info.was_estimated,
        applyUrl: info.apply_url || url,
        applyLabel: info.apply_label || 'Apply / learn more',
        actionItems: Array.isArray(info.action_items)
          ? info.action_items.slice(0, 5).map((ai, i) => ({
              id: `${opp.id}-t${i}`,
              text: ai.text,
              url: ai.url ?? null,
              state: 'not_started',
            }))
          : [],
      });
    } catch (err) {
      console.error(`Failed to fetch details for ${opp.name}:`, (err as Error).message);
      await addTrackerItem(bucket, {
        id: opp.id,
        name: opp.name,
        url,
        type,
        bucket,
        progressStatus: 'not_started',
        status: 'unknown',
        reviewStatus,
        reviewSummary,
        meta: [opp.org, opp.type, opp.price, opp.location].filter(Boolean).join(' · '),
        fit: reason || summary,
        note: "Live details couldn't be fetched — showing database info only. Check the official site directly.",
        noteType: 'flag',
        importantDates: [],
        deadlineLabel: 'CHECK SITE',
        wasEstimated: false,
        applyUrl: url,
        applyLabel: 'Visit site',
        actionItems: [],
      });
    }
  }

  async function addSelectedToTracker() {
    if (!selected.size || adding) return;
    setAdding(true);
    const ids = [...selected];
    setAddProgress({ done: 0, total: ids.length });
    try {
      for (let i = 0; i < ids.length; i++) {
        const r = results.find((x) => x.opp.id === ids[i]);
        if (r) await addOneToTracker(r.opp, r.reason);
        setAddProgress({ done: i + 1, total: ids.length });
      }
      // Only this batch carries the NEW treatment in the Quest Log — see markNewlyAdded.
      markNewlyAdded(selected);
      setTrackedIds((p) => new Set([...p, ...selected]));
      setSelected(new Set());
      // Adding is the point of departure to the Quest Log — land there instead of leaving
      // the student on a Fresh Finds page that now just shows the same cards as "tracked".
      router.push('/(app)/tracker');
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
        if (set.size && !set.has((r.opp[f.key] as string) ?? '')) return false;
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
        <SoftCard style={styles.heroCard}>
          {!profileLoaded ? (
            <View style={styles.loadingRow}>
              <ActivityIndicator color={colors.orangeDeep} size="small" />
              <View style={styles.flex1}>
                <Text style={styles.heroTitleSm}>Loading your profile…</Text>
              </View>
            </View>
          ) : searching ? (
            <View style={styles.loadingRow}>
              <ActivityIndicator color={colors.orangeDeep} size="small" />
              <View style={styles.flex1}>
                <Text style={styles.heroTitleSm}>Finding your matches…</Text>
                <Text style={styles.heroSub}>Searching based on everything in your profile.</Text>
              </View>
            </View>
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
              <Text style={[styles.heroSub, styles.heroSubItalic]}>Based on everything in your profile.</Text>
              <PopButton label="View my matches →" onPress={() => setStage('results')} style={styles.selfStart} />
            </>
          ) : (
            <>
              <Text style={styles.heroTitle}>Fresh Finds</Text>
              <Text style={[styles.heroSub, styles.heroSubItalic]}>We'll use your profile to surface the best fits.</Text>
              <PopButton label="Suggest opportunities for me" onPress={suggestForMe} style={styles.selfStart} />
            </>
          )}
        </SoftCard>

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

      {/* Filter row */}
      <View style={styles.filterBar}>
        <Text style={styles.filterLabel}>FILTER:</Text>
        {profileTags.length > 0 && (
          <View>
            <Pressable
              {...profileFacetPop.handlers}
              style={[styles.filterToggle, profileFacetPop.shadowStyle]}
              onPress={() => setOpenFacet(openFacet === 'profile' ? null : 'profile')}
            >
              <Text style={styles.filterToggleText}>▾ Your Profile{selectedTag ? ' (1)' : ''}</Text>
            </Pressable>
            {openFacet === 'profile' && (
              <View style={[styles.facetPanel, styles.facetPanelWide]}>
                <Pressable style={styles.facetRow} onPress={() => { setSelectedTag(null); setVisibleCount(10); setOpenFacet(null); }}>
                  <Text style={styles.facetRowText}>{selectedTag ? '○' : '●'} None</Text>
                </Pressable>
                {profileTags.map((t) => (
                  <Pressable key={t.tag} style={styles.facetRow} onPress={() => { setSelectedTag(t.tag); setVisibleCount(10); setOpenFacet(null); }}>
                    <Text style={styles.facetRowText}>{selectedTag === t.tag ? '●' : '○'} {t.tag}</Text>
                  </Pressable>
                ))}
              </View>
            )}
          </View>
        )}
        <Pressable style={[styles.filterToggle, untrackedOnly && styles.filterToggleOn]} onPress={() => setUntrackedOnly(!untrackedOnly)}>
          <Text style={styles.filterToggleText}>{untrackedOnly ? '☑' : '☐'} Only untracked</Text>
        </Pressable>
        {FILTER_FIELDS.map((f) => {
          const values = [...new Set(sortedResults.map((r) => (r.opp[f.key] as string) ?? '').filter(Boolean))].sort();
          if (values.length < 2) return null;
          const active = filters[f.key].size;
          return (
            <View key={f.key}>
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
                onPress={() => setOpenFacet(openFacet === f.key ? null : f.key)}
              >
                <Text style={styles.filterToggleText}>▾ {f.label}{active ? ` (${active})` : ''}</Text>
              </Pressable>
              {openFacet === f.key && (
                <View style={styles.facetPanel}>
                  {values.map((v) => (
                    <Pressable key={v} style={styles.facetRow} onPress={() => toggleFilter(f.key, v)}>
                      <Text style={styles.facetRowText}>{filters[f.key].has(v) ? '☑' : '☐'} {v}</Text>
                    </Pressable>
                  ))}
                </View>
              )}
            </View>
          );
        })}
      </View>
      {!!note && <Text style={styles.note}>{note}</Text>}
      {tagScoring && <Text style={styles.note}>Scoring matches against your profile…</Text>}

      {/* Result cards */}
      {visibleResults.map(({ opp, reason, tier, aiReasoning, aiRank }) => {
        const isSelected = selected.has(opp.id);
        const isTracked = trackedIds.has(opp.id);
        const cat = suggestMode ? (KIND_CONFIG[kindForOpp(opp)]?.name ?? 'Opportunity') : KIND_CONFIG[kind].name;
        const reviewed = opp.review_status === 'positive';
        const mixed = opp.review_status === 'mixed';
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
                {reviewed && <MiniBadge label="Well reviewed" bg={colors.emerald100} fg={colors.emerald900} />}
                {mixed && <MiniBadge label="Mixed reviews" bg="#FFEDD5" fg="#7C2D12" />}
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
        <View style={styles.facetPanel}>
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
  link: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.muted, textDecorationLine: 'underline' },

  heroCard: { padding: 40, gap: 8 },
  loadingRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
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
  facetPanel: { position: 'absolute', top: '100%', left: 0, marginTop: 8, width: 224, backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.lg, padding: 12, zIndex: 50, gap: 2 },
  facetPanelWide: { width: 320 },
  facetRow: { paddingVertical: 4 },
  facetRowText: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 16, color: colors.slate900 },

  resultCard: { backgroundColor: colors.white, borderWidth: 4, borderColor: colors.slate900, borderRadius: radius.xxl, padding: 24, gap: 16 },
  resultCardHovered: { transform: [{ translateX: -2 }, { translateY: -2 }] },
  resultCardSelected: { borderColor: '#A3E635', backgroundColor: '#F7FEE7' },
  cardTopRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' },
  badgeRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap', flexShrink: 1 },
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
